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
terraform init
terraform apply \
  -var "container_image=${IMAGE_URI}" \
  -var "openai_api_key_arn=arn:aws:secretsmanager:us-east-1:123...:secret:openai-xyz" \
  -var "pinecone_api_key_arn=arn:aws:secretsmanager:us-east-1:123...:secret:pinecone-xyz"
```

## Notes

The listener currently uses a self-signed ACM cert so `terraform apply` works
out of the box without a registered domain — swap in a real ACM cert for
production by replacing `aws_acm_certificate.self` with a data source on your
own domain's cert.

Lambda resources (S3 parser, embedding, webhook) are deployed via a separate
packaging step (`scripts/deploy_lambdas.sh`) so Terraform doesn't have to
rebuild the code on every infra change.
