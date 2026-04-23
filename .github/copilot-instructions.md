# GitHub Copilot Instructions — VecturaFlow

Tool identifier: copilot

## Session Read Order (every session — ~4K tokens total)

1. **`graphify/INDEX.md`** (~1.4K tokens) — open in a split pane before asking
   architecture questions. Contains: layout, hard rules, data flow summary, cross-editor table.
2. **`.ai/CONTEXT.md`** (~300 tokens) — what the last tool was working on.
3. **`.ai/MEMORY.md`** (~2K tokens) — invariants that cannot be violated.

## Before Touching Any File

Check `graphify/modules/<module>-<file>.md` for the file's purpose, key functions, and
imports. This saves you reading the full source file.

Example: before editing `api/main.py`, read `graphify/modules/api-main.md` first.

## Invariants (from .ai/MEMORY.md — abbreviated)

- No `os.environ` direct access in `api/` — always `from api.config import settings`
- No `print()` — use `logger` from `api.logger` or `ingestion.logging_util`
- Chunk per TextBlock, not per document (preserves page/section for citations)
- Module-level boto3 clients only (Lambda warm-start requirement)
- DynamoDB: never `scan()` — always use GSI `status-ingested_at-index` with `query()`
- Pydantic v2: `model_dump()` not `dict()`, `model_validate()` not `parse_obj()`
- Tests: `@mock_aws` from moto — never hit real AWS

## Session End

Append to `.ai/CHANGELOG-AI.md` with `TOOL=copilot` in the entry header.
Overwrite `.ai/CONTEXT.md` with current state.
If structure changed: run `python scripts/graphify.py`

## Stack Reference

See `.ai/deps.md` for pinned versions and official doc links.
See `graphify/decisions.md` for architectural tradeoffs.

## Author

Jagadeesh Pamidi — `jagadeesh6187@gmail.com`
