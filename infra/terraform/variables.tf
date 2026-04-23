variable "project" {
  description = "Short project name used as a prefix on every resource"
  type        = string
  default     = "vecturaflow"
}

variable "env" {
  description = "Deployment environment (prod | staging | dev)"
  type        = string
  default     = "prod"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for the VecturaFlow VPC"
  type        = string
  default     = "10.40.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDRs (one per AZ — ALB + NAT)"
  type        = list(string)
  default     = ["10.40.0.0/24", "10.40.1.0/24"]
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs (one per AZ — ECS tasks)"
  type        = list(string)
  default     = ["10.40.10.0/24", "10.40.11.0/24"]
}

variable "container_image" {
  description = "Full ECR image URI for the API container (e.g. 123456.dkr.ecr.us-east-1.amazonaws.com/vecturaflow:abc123)"
  type        = string
}

variable "container_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)"
  type        = number
  default     = 512
}

variable "container_memory" {
  description = "Fargate task memory (MiB)"
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Number of running API tasks"
  type        = number
  default     = 2
}

variable "min_capacity" {
  description = "Autoscaling minimum"
  type        = number
  default     = 2
}

variable "max_capacity" {
  description = "Autoscaling maximum"
  type        = number
  default     = 8
}

variable "pinecone_index" {
  description = "Pinecone index name"
  type        = string
  default     = "vecturaflow"
}

variable "openai_api_key_arn" {
  description = "Secrets Manager ARN holding the OpenAI API key"
  type        = string
}

variable "pinecone_api_key_arn" {
  description = "Secrets Manager ARN holding the Pinecone API key"
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the public ALB HTTPS listener"
  type        = string
}

variable "alarm_actions" {
  description = "SNS topic ARNs or other CloudWatch alarm action ARNs"
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags applied to every resource"
  type        = map(string)
  default = {
    Project = "VecturaFlow"
    Owner   = "Jagadeesh Pamidi"
  }
}
