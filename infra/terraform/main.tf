# ─────────────────────────────────────────────────────────────────────────────
# VecturaFlow infrastructure — AWS / ECS Fargate
#
# Topology:
#   VPC  (2 public + 2 private subnets, 1 NAT)
#   ALB  in public subnets, TLS-terminating, forwards :443 → target group :8000
#   ECS  Fargate cluster running the API container in private subnets
#   SQS  two queues (ingestion, embedding) + DLQs
#   DDB  two tables (registry with GSI, keys)
#   S3   one bucket for ingestion with event → ingestion queue
#   Lambda  three (s3 ingestion, parser, embedder) — deployed via separate
#           zip artifacts; this file wires the roles + triggers
#   ECR  one repo for the API container
# ─────────────────────────────────────────────────────────────────────────────

provider "aws" {
  region = var.region
  default_tags {
    tags = merge(var.tags, { Env = var.env })
  }
}

locals {
  name   = "${var.project}-${var.env}"
  azs    = slice(data.aws_availability_zones.available.names, 0, length(var.public_subnet_cidrs))
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

# ── VPC ──────────────────────────────────────────────────────────────────────

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "${local.name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-eip" }
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.this]
  tags          = { Name = "${local.name}-nat" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${local.name}-public-rt" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = { Name = "${local.name}-private-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── Security groups ──────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb-sg"
  description = "Allow 443 from internet"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api-sg"
  description = "ECS tasks - only ALB may reach :8000"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── ECR ──────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "api" {
  name                 = "${local.name}-api"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

# ── S3 ingestion bucket ──────────────────────────────────────────────────────

resource "aws_s3_bucket" "ingestion" {
  bucket        = "${local.name}-ingestion-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "ingestion" {
  bucket = aws_s3_bucket.ingestion.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "ingestion" {
  bucket                  = aws_s3_bucket.ingestion.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ingestion" {
  bucket = aws_s3_bucket.ingestion.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# ── SQS ──────────────────────────────────────────────────────────────────────

resource "aws_sqs_queue" "ingestion_dlq" {
  name                      = "${local.name}-ingestion-dlq"
  message_retention_seconds = 1209600 # 14 days
}

resource "aws_sqs_queue" "ingestion" {
  name                       = "${local.name}-ingestion"
  visibility_timeout_seconds = 180
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingestion_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "embedding_dlq" {
  name                      = "${local.name}-embedding-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "embedding" {
  name                       = "${local.name}-embedding"
  visibility_timeout_seconds = 120
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.embedding_dlq.arn
    maxReceiveCount     = 5
  })
}

# Allow S3 → SQS (ingestion)
data "aws_iam_policy_document" "ingestion_queue_policy" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingestion.arn]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.ingestion.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "ingestion" {
  queue_url = aws_sqs_queue.ingestion.id
  policy    = data.aws_iam_policy_document.ingestion_queue_policy.json
}

resource "aws_s3_bucket_notification" "ingestion" {
  bucket = aws_s3_bucket.ingestion.id
  queue {
    queue_arn = aws_sqs_queue.ingestion.arn
    events    = ["s3:ObjectCreated:*"]
  }
  depends_on = [aws_sqs_queue_policy.ingestion]
}

# ── DynamoDB ─────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "registry" {
  name         = "${local.name}-registry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "doc_id"

  attribute {
    name = "doc_id"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "ingested_at"
    type = "S"
  }

  global_secondary_index {
    name            = "status-ingested_at-index"
    hash_key        = "status"
    range_key       = "ingested_at"
    projection_type = "ALL"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
}

resource "aws_dynamodb_table" "keys" {
  name         = "${local.name}-keys"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "api_key"

  attribute {
    name = "api_key"
    type = "S"
  }

  point_in_time_recovery { enabled = true }
  server_side_encryption { enabled = true }
}

# ── CloudWatch log groups ────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}-api"
  retention_in_days = 30
}

# ── ECS cluster + task role ──────────────────────────────────────────────────

resource "aws_ecs_cluster" "this" {
  name = "${local.name}-cluster"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_exec" {
  name               = "${local.name}-task-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "task_exec" {
  role       = aws_iam_role.task_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow the execution role to read the secrets (needed so ECS can inject them)
data "aws_iam_policy_document" "secrets_read" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.openai_api_key_arn, var.pinecone_api_key_arn]
  }
}

resource "aws_iam_role_policy" "task_exec_secrets" {
  role   = aws_iam_role.task_exec.id
  policy = data.aws_iam_policy_document.secrets_read.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:Query", "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.registry.arn,
      "${aws_dynamodb_table.registry.arn}/index/*",
      aws_dynamodb_table.keys.arn,
    ]
  }
  statement {
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.ingestion.arn, aws_sqs_queue.embedding.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.ingestion.arn}/*"]
  }
  statement {
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ── Task definition ──────────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.task_exec.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = var.container_image
    essential = true
    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]
    environment = [
      { name = "API_ENV",            value = var.env },
      { name = "AWS_DEFAULT_REGION", value = var.region },
      { name = "PINECONE_INDEX",     value = var.pinecone_index },
      { name = "INGESTION_BUCKET",   value = aws_s3_bucket.ingestion.id },
      { name = "INGESTION_QUEUE_URL", value = aws_sqs_queue.ingestion.id },
      { name = "EMBEDDING_QUEUE_URL", value = aws_sqs_queue.embedding.id },
      { name = "REGISTRY_TABLE",     value = aws_dynamodb_table.registry.name },
      { name = "KEYS_TABLE",         value = aws_dynamodb_table.keys.name },
    ]
    secrets = [
      { name = "OPENAI_API_KEY",   valueFrom = var.openai_api_key_arn },
      { name = "PINECONE_API_KEY", valueFrom = var.pinecone_api_key_arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "api"
      }
    }
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 20
    }
  }])
}

# ── ALB + target group ───────────────────────────────────────────────────────

resource "aws_lb" "this" {
  name               = "${local.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  idle_timeout       = 60
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.this.id
  target_type = "ip"

  health_check {
    path                = "/health"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# Production note: replace the self-signed listener cert with an ACM-issued one
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.self.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "tls_private_key" "self" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "self" {
  private_key_pem = tls_private_key.self.private_key_pem
  subject { common_name = "${local.name}.internal" }
  validity_period_hours = 8760
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "aws_acm_certificate" "self" {
  private_key      = tls_private_key.self.private_key_pem
  certificate_body = tls_self_signed_cert.self.cert_pem
}

# ── ECS service ──────────────────────────────────────────────────────────────

resource "aws_ecs_service" "api" {
  name                               = "${local.name}-api"
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.api.arn
  desired_count                      = var.desired_count
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.https]
}

# ── Autoscaling (CPU-based) ──────────────────────────────────────────────────

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.min_capacity
  max_capacity       = var.max_capacity
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    scale_in_cooldown  = 120
    scale_out_cooldown = 30
  }
}
