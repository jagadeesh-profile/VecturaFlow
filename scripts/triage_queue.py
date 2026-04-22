#!/usr/bin/env python3
"""
VecturaFlow — ingestion queue triage.

Inspects the visible messages in the ingestion queue and classifies each one
as DRAIN, REPROCESS, or DLQ using the live DynamoDB registry.

Decision rule:
  DRAIN if: valid JSON AND registry status == "embedded"
  REPROCESS if: valid JSON AND (doc missing OR status in {"failed", "pending"})
  DLQ if: malformed / unsupported schema / receive_count >= N (default 5)

Dry-run is the default. Use --apply to actually delete or dead-letter.

Usage:
    python scripts/triage_queue.py
    python scripts/triage_queue.py --apply
    python scripts/triage_queue.py --apply --max-receives 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import boto3
from botocore.exceptions import ClientError

from api.config import settings


SUPPORTED_FILE_TYPES = {"pdf", "docx", "csv", "txt", "json"}


@dataclass
class TriageRow:
    msg_id: str
    doc_id: str
    receive_count: int
    age_seconds: int
    decision: str
    reason: str
    receipt_handle: str
    body: str


def _source_queue() -> Any:
    return boto3.client("sqs", region_name=settings.aws_default_region)


def _registry_table() -> Any:
    dynamo = boto3.resource("dynamodb", region_name=settings.aws_default_region)
    return dynamo.Table(settings.registry_table)


def _sha256_doc_id(bucket: str, key: str) -> str:
    return hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()


def _resolve_queue_name(queue_url: str) -> str:
    return queue_url.rstrip("/").rsplit("/", 1)[-1]


def _resolve_dlq_url(sqs: Any, queue_url: str) -> str | None:
    try:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["RedrivePolicy"],
        )
        policy_raw = attrs.get("Attributes", {}).get("RedrivePolicy")
        if not policy_raw:
            return None
        policy = json.loads(policy_raw)
        dead_letter_arn = policy.get("deadLetterTargetArn")
        if not dead_letter_arn:
            return None
        dlq_name = dead_letter_arn.rsplit(":", 1)[-1]
        return sqs.get_queue_url(QueueName=dlq_name)["QueueUrl"]
    except Exception:
        return None


def _extract_doc_id(body: str) -> tuple[str | None, str | None]:
    """Return (doc_id, error_reason)."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, f"malformed JSON: {exc.msg}"

    if not isinstance(payload, dict):
        return None, "unsupported schema: top-level JSON is not an object"

    doc_id = payload.get("doc_id")
    if isinstance(doc_id, str) and doc_id.strip():
        return doc_id, None

    records = payload.get("Records")
    if isinstance(records, list) and records:
        first = records[0]
        if isinstance(first, dict):
            s3 = first.get("s3")
            if isinstance(s3, dict):
                bucket = (s3.get("bucket") or {}).get("name")
                key = (s3.get("object") or {}).get("key")
                if bucket and key:
                    return _sha256_doc_id(bucket, key.replace("+", " ")), None
                return None, "unsupported schema: S3 record missing bucket/key"

    return None, "unsupported schema: expected doc_id or S3 Records payload"


def _registry_status(doc_id: str) -> tuple[str | None, str | None]:
    try:
        response = _registry_table().get_item(
            Key={"doc_id": doc_id},
            ProjectionExpression="#s",
            ExpressionAttributeNames={"#s": "status"},
        )
        item = response.get("Item")
        if not item:
            return None, None
        return item.get("status"), None
    except ClientError as exc:
        return None, f"registry lookup failed: {exc.response['Error'].get('Message', str(exc))}"


def _age_seconds(sent_ts_ms: str | None) -> int:
    if not sent_ts_ms:
        return 0
    try:
        sent = int(sent_ts_ms) / 1000.0
    except (TypeError, ValueError):
        return 0
    now = datetime.now(timezone.utc).timestamp()
    return max(0, int(now - sent))


def _format_age(age_seconds: int) -> str:
    if age_seconds < 60:
        return f"{age_seconds}s"
    minutes, seconds = divmod(age_seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _classify_message(
    message: dict[str, Any],
    max_receives: int,
) -> TriageRow:
    msg_id = message.get("MessageId", "unknown")
    receipt_handle = message.get("ReceiptHandle", "")
    attrs = message.get("Attributes", {}) or {}
    receive_count = int(attrs.get("ApproximateReceiveCount", "0") or 0)
    age_seconds = _age_seconds(attrs.get("SentTimestamp"))
    body = message.get("Body", "")

    doc_id, error = _extract_doc_id(body)

    if receive_count >= max_receives:
        return TriageRow(
            msg_id=msg_id,
            doc_id=doc_id or "-",
            receive_count=receive_count,
            age_seconds=age_seconds,
            decision="DLQ",
            reason=f"receive_count {receive_count} >= {max_receives}",
            receipt_handle=receipt_handle,
            body=body,
        )

    if error:
        return TriageRow(
            msg_id=msg_id,
            doc_id=doc_id or "-",
            receive_count=receive_count,
            age_seconds=age_seconds,
            decision="DLQ",
            reason=error,
            receipt_handle=receipt_handle,
            body=body,
        )

    if not doc_id:
        return TriageRow(
            msg_id=msg_id,
            doc_id="-",
            receive_count=receive_count,
            age_seconds=age_seconds,
            decision="DLQ",
            reason="unsupported schema: no doc_id could be resolved",
            receipt_handle=receipt_handle,
            body=body,
        )

    status, registry_error = _registry_status(doc_id)
    if registry_error:
        return TriageRow(
            msg_id=msg_id,
            doc_id=doc_id,
            receive_count=receive_count,
            age_seconds=age_seconds,
            decision="REPROCESS",
            reason=registry_error,
            receipt_handle=receipt_handle,
            body=body,
        )

    if status == "embedded":
        return TriageRow(
            msg_id=msg_id,
            doc_id=doc_id,
            receive_count=receive_count,
            age_seconds=age_seconds,
            decision="DRAIN",
            reason="registry status=embedded",
            receipt_handle=receipt_handle,
            body=body,
        )

    if status in {"failed", "pending"}:
        return TriageRow(
            msg_id=msg_id,
            doc_id=doc_id,
            receive_count=receive_count,
            age_seconds=age_seconds,
            decision="REPROCESS",
            reason=f"registry status={status}",
            receipt_handle=receipt_handle,
            body=body,
        )

    if status is None:
        reason = "registry missing"
    else:
        reason = f"registry status={status}"

    return TriageRow(
        msg_id=msg_id,
        doc_id=doc_id,
        receive_count=receive_count,
        age_seconds=age_seconds,
        decision="REPROCESS",
        reason=reason,
        receipt_handle=receipt_handle,
        body=body,
    )


def _print_table(rows: list[TriageRow]) -> None:
    headers = ["msg_id", "doc_id", "receive_count", "age", "decision", "reason"]
    table_rows = [
        [
            row.msg_id,
            row.doc_id,
            str(row.receive_count),
            _format_age(row.age_seconds),
            row.decision,
            row.reason,
        ]
        for row in rows
    ]

    widths = [len(h) for h in headers]
    for row in table_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(values: list[str]) -> str:
        return " | ".join(value.ljust(widths[i]) for i, value in enumerate(values))

    print(fmt_row(headers))
    print("-+-".join("-" * width for width in widths))
    for row in table_rows:
        print(fmt_row(row))


def _apply_actions(
    sqs: Any,
    source_queue_url: str,
    dlq_url: str | None,
    rows: list[TriageRow],
) -> None:
    for row in rows:
        if row.decision == "DRAIN":
            sqs.delete_message(QueueUrl=source_queue_url, ReceiptHandle=row.receipt_handle)
            print(f"  deleted {row.msg_id} as stale/duplicate")
        elif row.decision == "DLQ":
            if not dlq_url:
                raise RuntimeError("DLQ decision encountered but no dead-letter queue is configured")
            sqs.send_message(
                QueueUrl=dlq_url,
                MessageBody=row.body,
                MessageAttributes={
                    "triage_reason": {
                        "DataType": "String",
                        "StringValue": row.reason,
                    },
                    "source_message_id": {
                        "DataType": "String",
                        "StringValue": row.msg_id,
                    },
                },
            )
            sqs.delete_message(QueueUrl=source_queue_url, ReceiptHandle=row.receipt_handle)
            print(f"  moved {row.msg_id} to DLQ")
        else:
            print(f"  left {row.msg_id} visible for retry")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete stale messages or move DLQ candidates. Default is dry-run.",
    )
    parser.add_argument(
        "--max-receives",
        type=int,
        default=5,
        help="Dead-letter a message once ApproximateReceiveCount reaches this value.",
    )
    parser.add_argument(
        "--queue-url",
        default=settings.ingestion_queue_url,
        help="Override the source SQS queue URL.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of visible messages to inspect.",
    )
    args = parser.parse_args()

    sqs = _source_queue()
    dlq_url = _resolve_dlq_url(sqs, args.queue_url)

    response = sqs.receive_message(
        QueueUrl=args.queue_url,
        MaxNumberOfMessages=min(max(args.limit, 1), 10),
        AttributeNames=["All"],
        MessageAttributeNames=["All"],
        VisibilityTimeout=0,
        WaitTimeSeconds=0,
    )

    messages = response.get("Messages", [])
    if not messages:
        print("No visible messages found in the ingestion queue.")
        return 0

    rows = [_classify_message(message, args.max_receives) for message in messages]
    _print_table(rows)

    any_dlq = any(row.decision == "DLQ" for row in rows)
    if args.apply:
        _apply_actions(sqs, args.queue_url, dlq_url, rows)
    else:
        print("\nDry-run mode: no queue mutations were performed. Use --apply to act.")

    return 1 if any_dlq else 0


if __name__ == "__main__":
    raise SystemExit(main())