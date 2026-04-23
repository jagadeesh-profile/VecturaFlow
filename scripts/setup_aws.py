"""
VecturaFlow — AWS resource setup script.
Creates S3 bucket, SQS queues (main + DLQ), and DynamoDB tables.
Idempotent: safe to run multiple times.

Usage:
    python -m scripts.setup_aws
    python -m scripts.setup_aws --dry-run
    python -m scripts.setup_aws --region us-west-2
"""
from __future__ import annotations

import argparse
import json
import os

import boto3
from botocore.exceptions import ClientError


def create_s3_bucket(s3, region: str, bucket_name: str, dry_run: bool) -> None:
    print(f"\n  S3 bucket: {bucket_name}")
    if dry_run:
        print("    [DRY RUN] Skipped")
        return
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket_name)
        else:
            s3.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )

        # Block all public access
        s3.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )

        # Enable versioning for safety
        s3.put_bucket_versioning(
            Bucket=bucket_name,
            VersioningConfiguration={"Status": "Enabled"},
        )
        print("    Created + public access blocked + versioning enabled")

    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            print("    Already exists — skipped")
        else:
            raise


def create_sqs_queue(sqs, queue_name: str, dlq_arn: str | None, dry_run: bool) -> str | None:
    """Creates an SQS queue. Returns the queue URL."""
    print(f"\n  SQS queue: {queue_name}")
    if dry_run:
        print("    [DRY RUN] Skipped")
        return None
    try:
        attrs: dict = {
            "MessageRetentionPeriod": "86400",  # 1 day
            "VisibilityTimeout": "300",          # 5 min — enough for Lambda processing
        }
        if dlq_arn:
            attrs["RedrivePolicy"] = json.dumps({
                "deadLetterTargetArn": dlq_arn,
                "maxReceiveCount": "3",
            })

        response = sqs.create_queue(QueueName=queue_name, Attributes=attrs)
        url = response["QueueUrl"]
        print(f"    Created: {url}")
        return url
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "QueueAlreadyExists":
            response = sqs.get_queue_url(QueueName=queue_name)
            url = response["QueueUrl"]
            print(f"    Already exists: {url}")
            return url
        raise


def create_dynamodb_table(dynamo, table_name: str, dry_run: bool) -> None:
    print(f"\n  DynamoDB table: {table_name}")
    if dry_run:
        print("    [DRY RUN] Skipped")
        return
    try:
        dynamo.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "doc_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "doc_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "ingested_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    # Fixes DemoAgent bug: GSI for status query instead of full scan
                    "IndexName": "status-ingested_at-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "ingested_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",  # On-demand — no capacity planning needed
        )
        print("    Created with GSI on (status, ingested_at)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceInUseException":
            print("    Already exists — skipped")
        else:
            raise


def create_api_keys_table(dynamo, table_name: str, dry_run: bool) -> None:
    print(f"\n  DynamoDB table: {table_name}")
    if dry_run:
        print("    [DRY RUN] Skipped")
        return
    try:
        dynamo.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "api_key_hash", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "api_key_hash", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        # Wait for table to be created before returning to callers.
        waiter = dynamo.get_waiter("table_exists")
        waiter.wait(TableName=table_name)

        print("    Created with hashed API key partition key")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceInUseException":
            print("    Already exists — skipped")
        else:
            raise


def main():
    parser = argparse.ArgumentParser(description="Set up VecturaFlow AWS resources")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no changes")
    parser.add_argument("--region", default=None, help="AWS region (default: from env)")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    region = args.region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    bucket_name = os.environ.get("INGESTION_BUCKET", "vecturaflow-ingestion-dev")
    registry_table = os.environ.get("REGISTRY_TABLE", "vecturaflow-registry")
    keys_table = os.environ.get("KEYS_TABLE", "vecturaflow-keys")
    ingestion_queue = "vecturaflow-ingestion"
    ingestion_dlq = "vecturaflow-ingestion-dlq"
    embedding_queue = "vecturaflow-embedding"
    embedding_dlq = "vecturaflow-embedding-dlq"

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{'='*60}")
    print(f"{prefix}VecturaFlow AWS Setup — region: {region}")
    print(f"{'='*60}")

    s3 = boto3.client("s3", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    dynamo = boto3.client("dynamodb", region_name=region)

    print("\n── S3 ──────────────────────────────────────────────────────")
    create_s3_bucket(s3, region, bucket_name, args.dry_run)

    print("\n── SQS ─────────────────────────────────────────────────────")
    # Create DLQs first, then main queues with redrive policy
    ingest_dlq_url = create_sqs_queue(sqs, ingestion_dlq, None, args.dry_run)
    embed_dlq_url = create_sqs_queue(sqs, embedding_dlq, None, args.dry_run)

    ingest_dlq_arn = None
    embed_dlq_arn = None
    if ingest_dlq_url and not args.dry_run:
        attrs = sqs.get_queue_attributes(QueueUrl=ingest_dlq_url, AttributeNames=["QueueArn"])
        ingest_dlq_arn = attrs["Attributes"]["QueueArn"]
    if embed_dlq_url and not args.dry_run:
        attrs = sqs.get_queue_attributes(QueueUrl=embed_dlq_url, AttributeNames=["QueueArn"])
        embed_dlq_arn = attrs["Attributes"]["QueueArn"]

    create_sqs_queue(sqs, ingestion_queue, ingest_dlq_arn, args.dry_run)
    create_sqs_queue(sqs, embedding_queue, embed_dlq_arn, args.dry_run)

    print("\n── DynamoDB ────────────────────────────────────────────────")
    create_dynamodb_table(dynamo, registry_table, args.dry_run)
    create_api_keys_table(dynamo, keys_table, args.dry_run)

    print(f"\n{'='*60}")
    print(f"{prefix}Setup complete.")
    if not args.dry_run:
        print("\nNext steps:")
        print("  1. Copy queue URLs to .env (INGESTION_QUEUE_URL, EMBEDDING_QUEUE_URL)")
        print("  2. Run: python -m scripts.setup_pinecone")
        print("  3. Run: uvicorn api.main:app --reload")
        print("  4. For local Bearer dev auth, set API_DEV_BYPASS=true")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
