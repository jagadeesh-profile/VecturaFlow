#!/usr/bin/env bash
# Pre-computes expensive tree/find/git operations so AI tools don't re-run them.
# Run after: structural changes (new files/dirs), tool handoff, weekly refresh.
set -euo pipefail

cd "$(dirname "$0")/.."

# Directory tree (3 levels, ignoring noise)
find . -maxdepth 3 \
  \( -name ".venv" -o -name ".venv312" -o -name "__pycache__" -o -name "dist" \
     -o -name "build" -o -name ".git" -o -name "wheelhouse" -o -name ".pytest_cache" \
     -o -name ".ruff_cache" -o -name "node_modules" \) -prune \
  -o -print > .ai/tree.txt 2>/dev/null

# Hotspots — largest source files by LOC (top 30)
find . -type f \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.js" \) \
  -not -path "./.venv/*" -not -path "./.venv312/*" -not -path "./.git/*" \
  -not -path "./wheelhouse/*" -not -path "./__pycache__/*" \
  -exec wc -l {} + 2>/dev/null | sort -rn | head -30 > .ai/hotspots.txt

# Recent git activity (last 50 commits)
git log --oneline -50 > .ai/recent-commits.txt 2>/dev/null \
  || echo "(no git history)" > .ai/recent-commits.txt

echo "Manifest regenerated: .ai/tree.txt  .ai/hotspots.txt  .ai/recent-commits.txt"
