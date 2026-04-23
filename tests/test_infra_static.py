from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def test_deploy_workflow_builds_wheelhouse_before_docker_build():
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()

    wheelhouse_pos = deploy.find("Build wheelhouse")
    docker_build_pos = deploy.find("docker build")

    assert wheelhouse_pos != -1
    assert docker_build_pos != -1
    assert wheelhouse_pos < docker_build_pos


def test_sqs_visibility_timeouts_cover_lambda_timeouts():
    main_tf = (ROOT / "infra" / "terraform" / "main.tf").read_text()
    lambdas_tf = (ROOT / "infra" / "terraform" / "lambdas.tf").read_text()

    ingestion_visibility = int(
        re.search(
            r'resource "aws_sqs_queue" "ingestion" \{.*?visibility_timeout_seconds = (\d+)',
            main_tf,
            re.S,
        ).group(1)
    )
    embedding_visibility = int(
        re.search(
            r'resource "aws_sqs_queue" "embedding" \{.*?visibility_timeout_seconds = (\d+)',
            main_tf,
            re.S,
        ).group(1)
    )
    parser_timeout = int(
        re.search(
            r'resource "aws_lambda_function" "ingest_parser" \{.*?timeout\s+= (\d+)',
            lambdas_tf,
            re.S,
        ).group(1)
    )
    embed_timeout = int(
        re.search(
            r'resource "aws_lambda_function" "ingest_embed" \{.*?timeout\s+= (\d+)',
            lambdas_tf,
            re.S,
        ).group(1)
    )

    assert ingestion_visibility >= parser_timeout
    assert embedding_visibility >= embed_timeout


def test_terraform_uses_managed_acm_certificate_not_self_signed_tls():
    main_tf = (ROOT / "infra" / "terraform" / "main.tf").read_text()
    variables_tf = (ROOT / "infra" / "terraform" / "variables.tf").read_text()
    versions_tf = (ROOT / "infra" / "terraform" / "versions.tf").read_text()

    assert 'variable "acm_certificate_arn"' in variables_tf
    assert "certificate_arn   = var.acm_certificate_arn" in main_tf
    assert "tls_self_signed_cert" not in main_tf
    assert "tls_private_key" not in main_tf
    assert 'resource "aws_acm_certificate" "self"' not in main_tf
    assert 'source  = "hashicorp/tls"' not in versions_tf


def test_terraform_remote_state_backend_uses_s3_with_dynamodb_locking():
    backend_tf = (ROOT / "infra" / "terraform" / "backend.tf").read_text()
    backend_example = (ROOT / "infra" / "terraform" / "backend.example.hcl").read_text()

    assert 'backend "s3"' in backend_tf
    assert "bucket" in backend_example
    assert "key" in backend_example
    assert "dynamodb_table" in backend_example
    assert "encrypt = true" in backend_example


def test_cloudwatch_alarms_cover_api_health_latency_and_dlqs():
    main_tf = (ROOT / "infra" / "terraform" / "main.tf").read_text()

    for alarm_name in [
        'resource "aws_cloudwatch_metric_alarm" "api_5xx"',
        'resource "aws_cloudwatch_metric_alarm" "api_target_response_time"',
        'resource "aws_cloudwatch_metric_alarm" "ingestion_dlq_visible"',
        'resource "aws_cloudwatch_metric_alarm" "embedding_dlq_visible"',
        'resource "aws_cloudwatch_metric_alarm" "ingestion_queue_age"',
        'resource "aws_cloudwatch_metric_alarm" "embedding_queue_age"',
    ]:
        assert alarm_name in main_tf


def test_keys_table_uses_sha256_hash_as_partition_key():
    main_tf = (ROOT / "infra" / "terraform" / "main.tf").read_text()

    keys_table = re.search(
        r'resource "aws_dynamodb_table" "keys" \{(?P<body>.*?)\n\}',
        main_tf,
        re.S,
    ).group("body")

    assert 'hash_key     = "api_key_hash"' in keys_table
    assert 'name = "api_key_hash"' in keys_table
    assert 'hash_key     = "api_key"' not in keys_table


def test_keys_table_uses_v2_name_for_safe_hash_key_migration():
    main_tf = (ROOT / "infra" / "terraform" / "main.tf").read_text()

    keys_table = re.search(
        r'resource "aws_dynamodb_table" "keys" \{(?P<body>.*?)\n\}',
        main_tf,
        re.S,
    ).group("body")

    assert 'name         = "${local.name}-keys-v2"' in keys_table


def test_lambda_image_installs_binary_wheels_only():
    dockerfile = (ROOT / "Dockerfile.lambda").read_text()
    requirements = (ROOT / "requirements.lambda.txt").read_text()

    assert "--only-binary=:all:" in dockerfile
    assert "tiktoken==0.7.0" in requirements


def test_lambda_environment_does_not_set_reserved_region_keys():
    lambdas_tf = (ROOT / "infra" / "terraform" / "lambdas.tf").read_text()

    assert "AWS_DEFAULT_REGION" not in lambdas_tf


def test_makefile_pushes_lambda_image_with_lambda_compatible_manifest():
    makefile = (ROOT / "Makefile").read_text()

    assert "lambda-image-push" in makefile
    assert "--platform linux/amd64" in makefile
    assert "--provenance=false" in makefile
    assert "--sbom=false" in makefile


def test_s3_sqs_lambda_filter_uses_valid_records_pattern_shape():
    lambdas_tf = (ROOT / "infra" / "terraform" / "lambdas.tf").read_text()

    assert "Records = {" in lambdas_tf
    assert "Records = [{" not in lambdas_tf
