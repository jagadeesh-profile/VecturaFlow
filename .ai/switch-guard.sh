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
cat >> .ai/CHANGELOG-AI.md <<HEREEOF

## [$TIMESTAMP] HANDOFF → $TOOL
Reason: $REASON
See .ai/CONTEXT.md for live state.
HEREEOF

# Update graph if Python source files changed since last commit
CHANGED=$(git diff --name-only HEAD 2>/dev/null | grep -E '\.(py)$' || true)
if [ -n "$CHANGED" ]; then
  echo "Source files changed — regenerating graphify knowledge layer..."
  python scripts/graphify.py 2>/dev/null \
    && git add graphify/ \
    && echo "graphify/ updated." \
    || echo "(graphify regeneration skipped — run manually: python scripts/graphify.py)"
fi

# Regenerate manifest cache
bash .ai/manifest.sh

# Commit the handoff snapshot
git add .ai/ 2>/dev/null || true
git commit -m "handoff: → $TOOL ($REASON)" 2>/dev/null \
  || echo "(nothing new to commit for handoff snapshot)"

# Print next-tool instructions
echo ""
echo "=== HANDOFF COMPLETE → $TOOL ==="
case "$TOOL" in
  codex)
    echo "1. Open VS Code at this folder"
    echo "2. Codex reads AGENTS.md automatically"
    echo "3. AGENTS.md read order: graphify/INDEX.md → .ai/CONTEXT.md → .ai/MEMORY.md"
    ;;
  copilot)
    echo "1. Open VS Code at this folder"
    echo "2. Copilot reads .github/copilot-instructions.md automatically"
    echo "3. Open graphify/INDEX.md in a split pane before asking architecture questions"
    ;;
  claude)
    echo "1. Run: claude --resume"
    echo "   OR start new session — CONTEXT.md has the state."
    ;;
esac
echo ""
echo "State saved to: .ai/CONTEXT.md"
echo "Log entry added: .ai/CHANGELOG-AI.md"
