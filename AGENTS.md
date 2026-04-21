# AGENTS.md

> **If you are an AI coding agent (Cursor, Aider, Codex, Continue, Windsurf,
> Copilot Chat, Claude Code, or a custom agent) opening this repository,
> read [`graphify/INDEX.md`](graphify/INDEX.md) first.**

`graphify/` is this project's portable brain. It contains:

| File                       | What it is                                                     |
|----------------------------|----------------------------------------------------------------|
| `graphify/INDEX.md`        | Entry point — orient yourself in 30 seconds.                   |
| `graphify/architecture.md` | Tech stack, layers, locked decisions.                          |
| `graphify/dataflow.md`     | The two pipelines (ingest + query) end to end.                 |
| `graphify/agents.md`       | Which of the 11 specialist agents owns which module.           |
| `graphify/glossary.md`     | Project vocabulary (doc_id, RAGState, MMR, confidence).        |
| `graphify/decisions.md`    | Every non-obvious tradeoff and why.                            |
| `graphify/modules/*.md`    | Per-file cards — purpose, imports, classes, functions.         |
| `graphify/graph.json`      | Machine-readable node + edge map of the entire repo.           |

**Hard rules** that override your defaults live in `graphify/INDEX.md § Hard rules`.
Read them before editing code.

## To refresh the memory after a structural change

```bash
python scripts/graphify.py
```

This rebuilds `graphify/modules/*.md` and `graphify/graph.json` from the live
source tree. Hand-written files (INDEX / architecture / agents / dataflow /
glossary / decisions) are never touched by the script.

## Notes for specific tools

- **Claude Code** also reads `CLAUDE.md` at the repo root.
- **Cursor / Windsurf / Aider / Codex CLI** read this file (`AGENTS.md`) by
  convention.
- **Continue** users: add `graphify/` to `contextProviders` in `config.json`.
- **Copilot Chat** users: open `graphify/INDEX.md` in a split pane before
  asking about architecture or cross-cutting changes.

## Author

Jagadeesh Pamidi — `jagadeesh6187@gmail.com`
