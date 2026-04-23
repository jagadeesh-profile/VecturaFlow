# ─────────────────────────────────────────────────────────────────────────────
# VecturaFlow — ingestion Lambdas (S3 → SQS → Lambda → SQS → Lambda → Pinecone)
#
# Topology:
#   S3 PUT → (already-wired) SQS ingestion → Lambda ingest-s3
#     → SQS ingestion (re-queued as doc metadata) → Lambda ingest-parser
#     → SQS embedding → Lambda ingest-embed → Pinecone
#
# One container image, three functions. Handler selected per function via
# image_config.command. Image built from ../../Dockerfile.lambda and pushed
# to aws_ecr_repository.ingest.
# ─────────────────────────────────────────────────────────────────────────────

# ── ECR repo for the ingest image ────────────────────────────────────────────

resource "aws_ecr_repository" "ingest" {
  name                 = "${local.name}-ingest"
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

variable "ingest_image" {
  description = "Full ECR image URI for the ingestion Lambda image (e.g. <account>.dkr.ecr.<region>.amazonaws.com/vecturaflow-prod-ingest:<tag>)"
  type        = string
}

# ── Shared IAM: assume role + common policy ─────────────────────────────────

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Everything the three Lambdas share: DynamoDB registry R/W, CloudWatch logs,
# CloudWatch custom metrics. S3/SQS/Secrets are per-function below.
data "aws_iam_policy_document" "lambda_common" {
  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name}-*:*"]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
  statement {
    sid = "Registry"
    actions = [
      "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.registry.arn,
      "${aws_dynamodb_table.registry.arn}/index/*",
    ]
  }
}

# ── lambda: ingest-s3  (SQS ingestion trigger → enqueue doc metadata) ───────

data "aws_iam_policy_document" "ingest_s3" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_common.json]
  statement {
    sid = "IngestionQueue"
    actions = [
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
      "sqs:SendMessage",
    ]
    resources = [aws_sqs_queue.ingestion.arn]
  }
}

resource "aws_iam_role" "ingest_s3" {
  name               = "${local.name}-ingest-s3"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ingest_s3" {
  role   = aws_iam_role.ingest_s3.id
  policy = data.aws_iam_policy_document.ingest_s3.json
}

resource "aws_cloudwatch_log_group" "ingest_s3" {
  name              = "/aws/lambda/${local.name}-ingest-s3"
  retention_in_days = 30
}

resource "aws_lambda_function" "ingest_s3" {
  function_name = "${local.name}-ingest-s3"
  role          = aws_iam_role.ingest_s3.arn
  package_type  = "Image"
  image_uri     = var.ingest_image
  timeout       = 60
  memory_size   = 512

  image_config {
    command = ["ingestion.lambda_s3.handler"]
  }

  environment {
    variables = {
      INGESTION_QUEUE_URL = aws_sqs_queue.ingestion.id
      REGISTRY_TABLE      = aws_dynamodb_table.registry.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest_s3]
}

resource "aws_lambda_event_source_mapping" "ingest_s3" {
  event_source_arn                   = aws_sqs_queue.ingestion.arn
  function_name                      = aws_lambda_function.ingest_s3.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]

  # Only route S3 event wrappers to lambda_s3. Messages with a `doc_id` field
  # (produced by lambda_s3 itself) go to lambda_parser via a second mapping.
  filter_criteria {
    filter {
      pattern = jsonencode({ body = { Records = { eventSource = ["aws:s3"] } } })
    }
  }
}

# ── lambda: ingest-parser  (SQS ingestion doc-metadata → embedding queue) ───

data "aws_iam_policy_document" "ingest_parser" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_common.json]
  statement {
    sid       = "S3Read"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.ingestion.arn}/*"]
  }
  statement {
    sid = "Queues"
    actions = [
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
      "sqs:SendMessage",
    ]
    resources = [aws_sqs_queue.ingestion.arn, aws_sqs_queue.embedding.arn]
  }
}

resource "aws_iam_role" "ingest_parser" {
  name               = "${local.name}-ingest-parser"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ingest_parser" {
  role   = aws_iam_role.ingest_parser.id
  policy = data.aws_iam_policy_document.ingest_parser.json
}

resource "aws_cloudwatch_log_group" "ingest_parser" {
  name              = "/aws/lambda/${local.name}-ingest-parser"
  retention_in_days = 30
}

resource "aws_lambda_function" "ingest_parser" {
  function_name = "${local.name}-ingest-parser"
  role          = aws_iam_role.ingest_parser.arn
  package_type  = "Image"
  image_uri     = var.ingest_image
  timeout       = 300
  memory_size   = 2048

  image_config {
    command = ["ingestion.lambda_parser.handler"]
  }

  environment {
    variables = {
      EMBEDDING_QUEUE_URL = aws_sqs_queue.embedding.id
      REGISTRY_TABLE      = aws_dynamodb_table.registry.name
      CHUNK_SIZE          = "512"
      CHUNK_OVERLAP       = "50"
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest_parser]
}

resource "aws_lambda_event_source_mapping" "ingest_parser" {
  event_source_arn                   = aws_sqs_queue.ingestion.arn
  function_name                      = aws_lambda_function.ingest_parser.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 10
  function_response_types            = ["ReportBatchItemFailures"]

  # Mirror of the S3 filter above — parser only sees doc-metadata messages.
  filter_criteria {
    filter {
      pattern = jsonencode({ body = { doc_id = [{ exists = true }] } })
    }
  }
}

# ── lambda: ingest-embed  (SQS embedding → OpenAI → Pinecone) ───────────────

data "aws_iam_policy_document" "ingest_embed" {
  source_policy_documents = [data.aws_iam_policy_document.lambda_common.json]
  statement {
    sid = "EmbeddingQueue"
    actions = [
      "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.embedding.arn]
  }
  statement {
    sid       = "Secrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.openai_api_key_arn, var.pinecone_api_key_arn]
  }
  statement {
    sid       = "FailedChunkDump"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.ingestion.arn}/failed-chunks/*"]
  }
}

resource "aws_iam_role" "ingest_embed" {
  name               = "${local.name}-ingest-embed"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "ingest_embed" {
  role   = aws_iam_role.ingest_embed.id
  policy = data.aws_iam_policy_document.ingest_embed.json
}

resource "aws_cloudwatch_log_group" "ingest_embed" {
  name              = "/aws/lambda/${local.name}-ingest-embed"
  retention_in_days = 30
}

resource "aws_lambda_function" "ingest_embed" {
  function_name = "${local.name}-ingest-embed"
  role          = aws_iam_role.ingest_embed.arn
  package_type  = "Image"
  image_uri     = var.ingest_image
  timeout       = 300
  memory_size   = 1024

  image_config {
    command = ["embeddings.lambda_embed.handler"]
  }

  environment {
    variables = {
      REGISTRY_TABLE       = aws_dynamodb_table.registry.name
      INGESTION_BUCKET     = aws_s3_bucket.ingestion.id
      PINECONE_INDEX       = var.pinecone_index
      EMBEDDING_MODEL      = "text-embedding-3-small"
      OPENAI_API_KEY_ARN   = var.openai_api_key_arn
      PINECONE_API_KEY_ARN = var.pinecone_api_key_arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest_embed]
}

resource "aws_lambda_event_source_mapping" "ingest_embed" {
  event_source_arn                   = aws_sqs_queue.embedding.arn
  function_name                      = aws_lambda_function.ingest_embed.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]
}

# ── Outputs ─────────────────────────────────────────────────────────────────

output "ingest_ecr_repository_url" {
  description = "Push the Lambda image here"
  value       = aws_ecr_repository.ingest.repository_url
}

output "ingest_lambda_arns" {
  description = "ARNs of the 3 ingestion Lambda functions"
  value = {
    s3     = aws_lambda_function.ingest_s3.arn
    parser = aws_lambda_function.ingest_parser.arn
    embed  = aws_lambda_function.ingest_embed.arn
  }
}
