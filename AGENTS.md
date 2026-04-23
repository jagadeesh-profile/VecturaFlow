# AGENTS.md — VecturaFlow

> If you are an AI coding agent (Cursor, Aider, Codex, Continue, Windsurf, Copilot Chat,
> Claude Code, or any AGENTS.md-compatible tool) opening this repository — read this file
> first, then follow the read order below.

## Session Read Order (every session, every tool — ~4K tokens total)

| Step | File | Cost | Purpose |
|------|------|------|---------|
| 1 | `graphify/INDEX.md` | ~1.4K tokens | Architecture orientation — what the project IS |
| 2 | `.ai/CONTEXT.md` | ~300 tokens | Live state — what the last tool was doing |
| 3 | `.ai/MEMORY.md` | ~2K tokens | Invariants — what you must never violate |

Do NOT scan source files before completing these 3 reads.

## What Each Layer Contains

### `graphify/` — Knowledge Layer (stable, editor-agnostic)

| File | What it is |
|------|-----------|
| `graphify/INDEX.md` | Entry point — orient in 30 seconds |
| `graphify/architecture.md` | Tech stack, layers, locked decisions |
| `graphify/dataflow.md` | Ingest + query pipelines end-to-end |
| `graphify/agents.md` | Which of the 11 specialist agents owns which module |
| `graphify/glossary.md` | Project vocabulary (doc_id, RAGState, MMR, confidence) |
| `graphify/decisions.md` | Every non-obvious tradeoff and why |
| `graphify/modules/*.md` | Per-file cards — purpose, imports, classes, functions |
| `graphify/graph.json` | Machine-readable node+edge map of the entire repo |

### `.ai/` — Session Layer (updated each session)

| File | What it is |
|------|-----------|
| `.ai/MEMORY.md` | Invariants + stack (read every session) |
| `.ai/CONTEXT.md` | Live handoff state (overwrite at session end) |
| `.ai/CHANGELOG-AI.md` | Append-only AI change log |
| `.ai/DECISIONS.md` | ADR register for new architectural decisions |
| `.ai/deps.md` | Pinned versions + official doc links |
| `.ai/tree.txt` | Pre-computed directory tree (use instead of find/ls) |
| `.ai/hotspots.txt` | Largest source files by LOC |
| `.ai/recent-commits.txt` | Last 50 commits (use instead of git log) |

## Token Discipline

- **Never** cold-read source files. Use `graphify/modules/` first.
- **Never** read a file >200 lines in full. Get line range from module card, read only that span.
- **Never** re-run `find`/`ls`/`git log` — use `.ai/tree.txt` / `hotspots.txt` / `recent-commits.txt`.

## Session End Protocol (append to .ai/CHANGELOG-AI.md)

```
## [ISO-TIMESTAMP] TOOL=<claude-code|codex|copilot>
Task: <one-line>
Files: <path> (L<start>-<end>)
Decision: <what you chose and why>
Next: <the single next action>
Blockers: <none | description>
```

Also overwrite `.ai/CONTEXT.md` with current task state.
If code structure changed: run `python scripts/graphify.py`

## Refreshing the Knowledge Layer

After structural changes (new module, renamed package, new import):

```bash
python scripts/graphify.py
```

Rebuilds `graphify/modules/*.md` and `graphify/graph.json` from live source.
Hand-written files (INDEX / architecture / agents / dataflow / glossary / decisions) are never
touched by the script — edit them by hand when a design decision actually changes.

## Tool Handoff

To snapshot state and switch tools:

```bash
bash .ai/switch-guard.sh codex "switching to codex for this task"
bash .ai/switch-guard.sh copilot "switching to copilot"
bash .ai/switch-guard.sh claude "resuming in claude"
```

## Author

Jagadeesh Pamidi — `jagadeesh6187@gmail.com`
