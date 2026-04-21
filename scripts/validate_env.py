"""
VecturaFlow — Environment Validation Script
============================================
Validates every required env var AND tests live connectivity to:
  - OpenAI API (embedding model)
  - Pinecone (index exists and is ready)
  - AWS (S3 bucket, SQS queues, DynamoDB tables)

Run this ONCE before starting the sprint.
All green = system is ready. Any red = fix before starting.

Usage:
    python scripts/validate_env.py
    python scripts/validate_env.py --skip-aws      # skip AWS checks (local only)
    python scripts/validate_env.py --skip-pinecone # skip Pinecone check
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Result model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    detail: str = ""


class Validator:
    def __init__(self):
        self.results: list[CheckResult] = []

    def check(self, name: str, fn) -> CheckResult:
        try:
            detail = fn()
            r = CheckResult(name=name, passed=True, message="OK", detail=detail or "")
        except Exception as exc:
            r = CheckResult(name=name, passed=False, message=str(exc)[:200])
        self.results.append(r)
        icon = "  \033[32m✓\033[0m" if r.passed else "  \033[31m✗\033[0m"
        detail_str = f"  → {r.detail}" if r.detail else ""
        print(f"{icon}  {r.name}")
        if not r.passed:
            print(f"      \033[31m{r.message}\033[0m")
        elif detail_str:
            print(f"  \033[90m{detail_str}\033[0m")
        return r

    def summary(self) -> bool:
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        print(f"\n{'─'*50}")
        if passed == total:
            print(f"\033[32m  ✓ All {total} checks passed. Ready to start sprint.\033[0m")
        else:
            failed = total - passed
            print(f"\033[31m  ✗ {failed}/{total} checks failed. Fix before starting.\033[0m")
            print("\n  Failed checks:")
            for r in self.results:
                if not r.passed:
                    print(f"    • {r.name}: {r.message}")
        print(f"{'─'*50}\n")
        return passed == total


# ─────────────────────────────────────────────────────────────────────────────
# Check functions
# ─────────────────────────────────────────────────────────────────────────────

def check_python_version() -> str:
    v = sys.version_info
    if v < (3, 11):
        raise RuntimeError(f"Python 3.11+ required, got {v.major}.{v.minor}.{v.micro}")
    return f"{v.major}.{v.minor}.{v.micro}"


def check_env_var(name: str, partial_mask: bool = True) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is not set. Add it to your .env file.")
    if val.endswith("...") or "ACCOUNT_ID" in val or val in ("sk-...", "pcsk_...", "AKIA..."):
        raise RuntimeError(f"{name} has placeholder value '{val}'. Replace with real value.")
    if partial_mask and len(val) > 8:
        masked = val[:6] + "..." + val[-4:]
        return masked
    return "(set)"


def check_openai_connectivity() -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    t0 = time.perf_counter()
    response = client.embeddings.create(
        model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        input=["VecturaFlow connectivity test"],
    )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    dim = len(response.data[0].embedding)
    assert dim == 1536, f"Expected 1536 dimensions, got {dim}"
    return f"model=text-embedding-3-small dim={dim} latency={latency_ms}ms"


def check_pinecone_connectivity() -> str:
    from pinecone import Pinecone
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index_name = os.environ.get("PINECONE_INDEX", "vecturaflow")
    existing = [idx.name for idx in pc.list_indexes()]
    if index_name not in existing:
        raise RuntimeError(
            f"Index '{index_name}' not found. "
            f"Run: make setup-pinecone\n"
            f"Existing indexes: {existing or ['(none)']}"
        )
    desc = pc.describe_index(index_name)
    ready = desc.status.get("ready", False)
    if not ready:
        raise RuntimeError(f"Index '{index_name}' exists but is not ready. Wait and retry.")
    stats = pc.Index(index_name).describe_index_stats()
    return f"index={index_name} dim={desc.dimension} vectors={stats.total_vector_count} ready=True"


def check_aws_credentials() -> str:
    import boto3
    from botocore.exceptions import NoCredentialsError, ClientError
    try:
        sts = boto3.client("sts", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        identity = sts.get_caller_identity()
        account = identity["Account"]
        arn = identity["Arn"]
        user = arn.split("/")[-1]
        return f"account={account[-4:]}**** user={user}"
    except NoCredentialsError:
        raise RuntimeError(
            "No AWS credentials found. Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in .env "
            "or configure AWS CLI with: aws configure"
        )


def check_s3_bucket() -> str:
    import boto3
    from botocore.exceptions import ClientError
    bucket = os.environ.get("INGESTION_BUCKET")
    if not bucket:
        raise RuntimeError("INGESTION_BUCKET not set")
    s3 = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    try:
        s3.head_bucket(Bucket=bucket)
        return f"bucket={bucket} accessible=True"
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "404":
            raise RuntimeError(f"Bucket '{bucket}' does not exist. Run: make setup-aws")
        if code == "403":
            raise RuntimeError(f"Bucket '{bucket}' exists but access denied. Check IAM permissions.")
        raise


def check_sqs_queues() -> str:
    import boto3
    from botocore.exceptions import ClientError
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    sqs = boto3.client("sqs", region_name=region)
    results = []
    for env_key, label in [
        ("INGESTION_QUEUE_URL", "ingestion"),
        ("EMBEDDING_QUEUE_URL", "embedding"),
    ]:
        url = os.environ.get(env_key)
        if not url:
            raise RuntimeError(f"{env_key} not set")
        try:
            attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages"])
            msg_count = attrs["Attributes"].get("ApproximateNumberOfMessages", "?")
            results.append(f"{label}=OK(msgs={msg_count})")
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "AWS.SimpleQueueService.NonExistentQueue":
                raise RuntimeError(f"Queue '{label}' does not exist. Run: make setup-aws")
            raise
    return " ".join(results)


def check_dynamodb_tables() -> str:
    import boto3
    from botocore.exceptions import ClientError
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    dynamo = boto3.client("dynamodb", region_name=region)
    results = []
    for env_key, label in [
        ("REGISTRY_TABLE", "registry"),
        ("KEYS_TABLE", "keys"),
    ]:
        table_name = os.environ.get(env_key, "vecturaflow-registry" if label == "registry" else "vecturaflow-keys")
        try:
            desc = dynamo.describe_table(TableName=table_name)
            status = desc["Table"]["TableStatus"]
            item_count = desc["Table"].get("ItemCount", 0)
            results.append(f"{label}={status}(items={item_count})")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                raise RuntimeError(f"Table '{table_name}' not found. Run: make setup-aws")
            raise
    return " ".join(results)


def check_dependencies_installed() -> str:
    missing = []
    packages = {
        "fastapi": "fastapi",
        "pinecone": "pinecone",
        "openai": "openai",
        "langchain": "langchain",
        "langgraph": "langgraph",
        "boto3": "boto3",
        "structlog": "structlog",
        "pydantic_settings": "pydantic-settings",
        "unstructured": "unstructured",
        "moto": "moto",
    }
    for module, pkg in packages.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise RuntimeError(f"Missing packages: {missing}. Run: pip install -r requirements.txt")
    return f"{len(packages)} packages verified"


def check_dotenv_file() -> str:
    env_path = ROOT / ".env"
    if not env_path.exists():
        raise RuntimeError(
            ".env file not found. Run: cp .env.example .env\n"
            "Then fill in your OPENAI_API_KEY, PINECONE_API_KEY, and AWS credentials."
        )
    content = env_path.read_text()
    placeholders = [line.split("=")[0] for line in content.splitlines()
                    if "=" in line and (line.endswith("...") or "ACCOUNT_ID" in line)]
    if placeholders:
        raise RuntimeError(f"Placeholder values found in .env for: {placeholders}")
    return ".env exists, no placeholders detected"


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VecturaFlow environment validation")
    parser.add_argument("--skip-aws", action="store_true", help="Skip AWS connectivity checks")
    parser.add_argument("--skip-pinecone", action="store_true", help="Skip Pinecone check")
    parser.add_argument("--skip-openai", action="store_true", help="Skip OpenAI API call")
    args = parser.parse_args()

    v = Validator()

    print("\n\033[1mVecturaFlow — Environment Validation\033[0m")
    print("=" * 50)

    print("\n\033[90m── System\033[0m")
    v.check("Python 3.11+", check_python_version)
    v.check("Dependencies installed", check_dependencies_installed)
    v.check(".env file exists", check_dotenv_file)

    print("\n\033[90m── Required env vars\033[0m")
    for var in ["OPENAI_API_KEY", "PINECONE_API_KEY", "PINECONE_INDEX",
                "INGESTION_BUCKET", "INGESTION_QUEUE_URL", "EMBEDDING_QUEUE_URL"]:
        v.check(f"Env: {var}", lambda k=var: check_env_var(k))

    if not args.skip_openai:
        print("\n\033[90m── OpenAI connectivity\033[0m")
        v.check("OpenAI API — embed test", check_openai_connectivity)

    if not args.skip_pinecone:
        print("\n\033[90m── Pinecone connectivity\033[0m")
        v.check("Pinecone — index ready", check_pinecone_connectivity)

    if not args.skip_aws:
        print("\n\033[90m── AWS connectivity\033[0m")
        v.check("AWS credentials", check_aws_credentials)
        v.check("S3 bucket accessible", check_s3_bucket)
        v.check("SQS queues exist", check_sqs_queues)
        v.check("DynamoDB tables exist", check_dynamodb_tables)

    success = v.summary()

    if success:
        print("  Next steps:")
        print("    make dev              → start FastAPI at localhost:8000")
        print("    make poc-local        → validate stack locally (no AWS)")
        print("    make poc              → full POC including Pinecone")
        print("")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
