#!/usr/bin/env bash
# Handoff between Claude Code → Codex → Copilot.
# Snapshots .ai/CONTEXT.md, appends to CHANGELOG-AI.md, then prints next-tool instructions.
# Usage: bash .ai/switch-guard.sh [claude|codex|copilot] [reason]
set -euo pipefail

TOOL="${1:-}"
REASON="${2:-manual-switch}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT"

if [ -z "$TOOL" ]; then
  echo "Usage: bash .ai/switch-guard.sh [claude|codex|copilot] [reason]"
  echo "  claude   → resume in Claude Code (claude --resume)"
  echo "  codex    → switch to OpenAI Codex in VS Code"
  echo "  copilot  → switch to GitHub Copilot"
  exit 1
fi

# Snapshot state to CHANGELOG-AI.md
cat >> .ai/CHANGELOG-AI.md <<EOF

## [$TIMESTAMP] HANDOFF → $TOOL
Reason: $REASON
See .ai/CONTEXT.md for live state.
