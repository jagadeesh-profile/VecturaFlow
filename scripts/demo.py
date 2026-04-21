"""
VecturaFlow — End-to-End Demo Script.

Showcases the complete VecturaFlow pipeline in ~90 seconds:
  1. Upload sample files to S3  → triggers FileIngestionAgent
  2. Poll DynamoDB per doc_id   → waits for status="embedded"
  3. Run 3 queries via the API  → shows answer + sources + confidence

Usage:
  python scripts/demo.py

Required environment variables (set in .env or shell):
  INGESTION_BUCKET        S3 bucket name
  REGISTRY_TABLE          DynamoDB table name (vecturaflow-registry)
  VECTURAFLOW_API_URL     e.g. http://localhost:8000  or  https://api.example.com
  VECTURAFLOW_API_KEY     API key (use "dev" for local development)

Optional:
  AWS_DEFAULT_REGION      (default: us-east-1)
  DEMO_POLL_INTERVAL      seconds between status polls (default: 5)
  DEMO_TIMEOUT            max seconds to wait for ingestion (default: 120)
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import time

import boto3
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
SAMPLE_DIR = Path(__file__).parent / "sample_data"

S3_BUCKET = os.environ.get("INGESTION_BUCKET", "")
REGISTRY_TABLE = os.environ.get("REGISTRY_TABLE", "vecturaflow-registry")
API_URL = os.environ.get("VECTURAFLOW_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("VECTURAFLOW_API_KEY", "dev")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
POLL_INTERVAL = int(os.environ.get("DEMO_POLL_INTERVAL", "5"))
TIMEOUT_SECS = int(os.environ.get("DEMO_TIMEOUT", "120"))

SAMPLE_FILES = [
    SAMPLE_DIR / "sample.txt",
    SAMPLE_DIR / "sample.csv",
]

DEMO_QUERIES = [
    "What is VecturaFlow and what does it do?",
    "What chunk size and embedding model does VecturaFlow use?",
    "How does VecturaFlow handle errors in the ingestion pipeline?",
]

# ─────────────────────────────────────────────────────────────────────────────
# AWS clients
# ─────────────────────────────────────────────────────────────────────────────

# Honour AWS_ENDPOINT_URL so the demo works against LocalStack
# (`docker-compose up` sets it to http://localhost:4566).
_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL") or None
_S3_CLIENT_KW = {"region_name": REGION}
_DDB_CLIENT_KW = {"region_name": REGION}
if _ENDPOINT:
    _S3_CLIENT_KW["endpoint_url"] = _ENDPOINT
    _DDB_CLIENT_KW["endpoint_url"] = _ENDPOINT

_s3 = boto3.client("s3", **_S3_CLIENT_KW)
_dynamo = boto3.resource("dynamodb", **_DDB_CLIENT_KW)
_registry = _dynamo.Table(REGISTRY_TABLE)

console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _doc_id_for_s3(bucket: str, key: str) -> str:
    """Mirror FileIngestionAgent's doc_id calculation: SHA256(bucket/key)."""
    return hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()


def _check_status(doc_id: str) -> str | None:
    """Fetch doc status from DynamoDB by primary key. Returns status string or None."""
    try:
        response = _registry.get_item(
            Key={"doc_id": doc_id},
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        item = response.get("Item")
        return item.get("status") if item else None
    except Exception:
        return None


def _query_api(question: str) -> dict:
    """POST a question to /v1/chat/completions and return parsed response."""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "vecturaflow",
        "messages": [{"role": "user", "content": question}],
    }
    response = httpx.post(
        f"{API_URL}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


# ─────────────────────────────────────────────────────────────────────────────
# Demo steps
# ─────────────────────────────────────────────────────────────────────────────

def step1_upload_files() -> list[tuple[str, str]]:
    """Upload sample files to S3. Returns list of (s3_key, doc_id) tuples."""
    console.rule("[bold blue]Step 1 — Upload sample files to S3")

    if not S3_BUCKET:
        console.print("[red]ERROR:[/red] INGESTION_BUCKET environment variable not set.")
        sys.exit(1)

    uploaded: list[tuple[str, str]] = []

    for file_path in SAMPLE_FILES:
        if not file_path.exists():
            console.print(f"[red]  ✗ File not found:[/red] {file_path}")
            sys.exit(1)

        s3_key = f"demo/{file_path.name}"
        doc_id = _doc_id_for_s3(S3_BUCKET, s3_key)

        try:
            _s3.upload_file(str(file_path), S3_BUCKET, s3_key)
            console.print(f"  [green]✓[/green] Uploaded [cyan]{s3_key}[/cyan]  doc_id=[dim]{doc_id[:12]}…[/dim]")
            uploaded.append((s3_key, doc_id))
        except Exception as exc:
            console.print(f"  [red]✗ Upload failed:[/red] {file_path.name} — {exc}")
            sys.exit(1)

    console.print(f"\n  [bold]{len(uploaded)} file(s) queued for ingestion.[/bold]\n")
    return uploaded


def step2_wait_for_embedding(uploads: list[tuple[str, str]]) -> int:
    """Poll DynamoDB per doc_id until all show status='embedded' or timeout."""
    console.rule("[bold blue]Step 2 — Wait for ingestion + embedding")

    doc_ids = [doc_id for _, doc_id in uploads]
    deadline = time.time() + TIMEOUT_SECS
    embedded_count = 0

    console.print(f"  Polling every {POLL_INTERVAL}s (timeout: {TIMEOUT_SECS}s)\n")

    while time.time() < deadline:
        statuses = {doc_id: _check_status(doc_id) for doc_id in doc_ids}
        embedded_count = sum(1 for s in statuses.values() if s == "embedded")

        # Build status table
        table = Table(show_header=True, header_style="bold")
        table.add_column("File", style="cyan")
        table.add_column("doc_id", style="dim")
        table.add_column("Status")

        for (s3_key, doc_id), status in zip(uploads, statuses.values(), strict=False):
            colour = "green" if status == "embedded" else "yellow" if status else "dim"
            table.add_row(
                Path(s3_key).name,
                doc_id[:16] + "…",
                f"[{colour}]{status or 'pending…'}[/{colour}]",
            )

        console.print(table)

        if embedded_count == len(doc_ids):
            console.print(f"\n  [bold green]✓ All {embedded_count} file(s) embedded.[/bold green]\n")
            return embedded_count

        remaining = int(deadline - time.time())
        console.print(f"  {embedded_count}/{len(doc_ids)} embedded — retrying in {POLL_INTERVAL}s  ({remaining}s left)\n")
        time.sleep(POLL_INTERVAL)

    console.print(
        f"\n  [yellow]⚠ Timeout after {TIMEOUT_SECS}s — "
        f"only {embedded_count}/{len(doc_ids)} files embedded.[/yellow]\n"
        "  The pipeline may still be processing. Try increasing DEMO_TIMEOUT."
    )
    return embedded_count


def step3_run_queries() -> None:
    """Send demo queries to the API and print answers with sources."""
    console.rule("[bold blue]Step 3 — Query the VecturaFlow API")

    for i, question in enumerate(DEMO_QUERIES, 1):
        console.print(f"\n  [bold]Q{i}:[/bold] {question}")

        start = time.perf_counter()
        try:
            data = _query_api(question)
        except httpx.HTTPStatusError as exc:
            console.print(f"  [red]✗ API error {exc.response.status_code}:[/red] {exc.response.text[:200]}")
            continue
        except Exception as exc:
            console.print(f"  [red]✗ Request failed:[/red] {exc}")
            continue

        latency_ms = int((time.perf_counter() - start) * 1000)
        answer = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        confidence = usage.get("confidence", "unknown")
        sources = usage.get("sources", [])

        conf_colour = "green" if confidence == "high" else "yellow" if confidence == "low" else "dim"
        console.print(f"\n  [bold]A:[/bold] {answer}")
        console.print(
            f"\n  [dim]Confidence:[/dim] [{conf_colour}]{confidence}[/{conf_colour}]  "
            f"[dim]|  Sources: {len(sources)}  |  Latency: {latency_ms}ms[/dim]"
        )

        if sources:
            src_table = Table(show_header=True, header_style="dim", box=None, padding=(0, 2))
            src_table.add_column("Source", style="cyan")
            src_table.add_column("Score", justify="right")
            src_table.add_column("Chunk")
            for s in sources[:3]:
                src_table.add_row(
                    Path(s.get("source", "")).name or s.get("source", ""),
                    f"{s.get('score', 0):.3f}",
                    str(s.get("chunk_index", 0)),
                )
            console.print(src_table)

        console.print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    start_total = time.perf_counter()

    console.print(Panel.fit(
        "[bold cyan]VecturaFlow — End-to-End Demo[/bold cyan]\n"
        "[dim]Autonomous agentic RAG platform on AWS[/dim]",
        border_style="cyan",
    ))
    console.print()

    # Validate env
    missing = [v for v in ("INGESTION_BUCKET", "REGISTRY_TABLE") if not os.environ.get(v)]
    if missing:
        console.print(f"[red]ERROR:[/red] Missing environment variables: {missing}")
        console.print("Set these in your .env file or shell before running the demo.")
        sys.exit(1)

    # Step 1: Upload
    uploads = step1_upload_files()

    # Step 2: Wait for embedding
    embedded = step2_wait_for_embedding(uploads)
    if embedded == 0:
        console.print("[red]No files were embedded — cannot run queries. Check pipeline logs.[/red]")
        sys.exit(1)

    # Step 3: Query
    step3_run_queries()

    # Summary
    total_s = int(time.perf_counter() - start_total)
    console.rule("[bold green]Demo Complete")
    console.print(
        f"  [bold]{len(uploads)}[/bold] file(s) ingested  |  "
        f"[bold]{len(DEMO_QUERIES)}[/bold] queries answered  |  "
        f"Total time: [bold]{total_s}s[/bold]"
    )
    console.print()


if __name__ == "__main__":
    main()
