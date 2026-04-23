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
