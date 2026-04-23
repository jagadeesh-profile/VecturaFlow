# VecturaFlow — Terraform (AWS ECS Fargate)

Provisions the full production footprint:

- VPC with two public + two private subnets and a NAT gateway
- ALB (HTTPS :443, redirect :80) → ECS service on port 8000
- ECS Fargate cluster running the `api` container in private subnets
- ECR repo for the container image
- Ingestion S3 bucket → SQS `ingestion` → Lambda parser → SQS `embedding` → Lambda embed
- DynamoDB `registry` (with `status-ingested_at-index` GSI) and `keys` tables
- CloudWatch log group + Container Insights
- Target-tracking autoscaling (CPU 60%) with a deployment circuit breaker

## Usage

```bash
cd infra/terraform

# 1. Build + push the image first (separately), then grab its URI
IMAGE_URI=123456789012.dkr.ecr.us-east-1.amazonaws.com/vecturaflow-prod-api:v0.1.0

# 2. Plan + apply
terraform init -backend-config=backend.example.hcl
terraform apply \
  -var "container_image=${IMAGE_URI}" \
  -var "openai_api_key_arn=arn:aws:secretsmanager:us-east-1:123...:secret:openai-xyz" \
  -var "pinecone_api_key_arn=arn:aws:secretsmanager:us-east-1:123...:secret:pinecone-xyz" \
  -var "acm_certificate_arn=arn:aws:acm:us-east-1:123...:certificate/..."
```

## Notes

The HTTPS listener requires an ACM-issued certificate ARN. Keep the certificate
in the same AWS region as the ALB and validate it before applying this stack.

Terraform state is configured as a partial S3 backend. Copy
`backend.example.hcl`, point it at the real state bucket and DynamoDB lock
table, then run `terraform init -migrate-state -backend-config=<file>` when
moving existing local state.

CloudWatch alarms are created for ALB 5xx responses, target response time, DLQ
depth, and SQS message age. Pass SNS topic ARNs via `alarm_actions` to page or
notify.

Lambda resources (S3 parser, embedding, webhook) are deployed via a separate
packaging step (`scripts/deploy_lambdas.sh`) so Terraform doesn't have to
rebuild the code on every infra change.
