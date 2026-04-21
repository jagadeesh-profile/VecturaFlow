output "alb_dns_name" {
  description = "Public DNS name of the VecturaFlow API ALB"
  value       = aws_lb.this.dns_name
}

output "ecr_repo_url" {
  description = "ECR repository URL for the API image"
  value       = aws_ecr_repository.api.repository_url
}

output "ingestion_bucket" {
  value = aws_s3_bucket.ingestion.id
}

output "ingestion_queue_url" {
  value = aws_sqs_queue.ingestion.id
}

output "embedding_queue_url" {
  value = aws_sqs_queue.embedding.id
}

output "registry_table" {
  value = aws_dynamodb_table.registry.name
}

output "keys_table" {
  value = aws_dynamodb_table.keys.name
}
