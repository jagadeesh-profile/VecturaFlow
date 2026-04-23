#!/usr/bin/env bash
# Pre-commit hook for VecturaFlow.
# 1. Protects .ai/MEMORY.md from unauthorized edits.
# 2. Auto-regenerates graphify/ when Python source files change.
# 3. Refreshes .ai/ manifest cache.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# 1. Block unauthorized .ai/MEMORY.md edits
if git diff --cached --name-only | grep -q "^\.ai/MEMORY\.md$"; then
  if [ -z "${ALLOW_MEMORY_EDIT:-}" ]; then
    echo ""
    echo "ERROR: .ai/MEMORY.md is write-protected."
    echo "  This file is the single source of truth for project invariants."
    echo "  To edit it intentionally, run:"
    echo "    ALLOW_MEMORY_EDIT=1 git commit ..."
    echo ""
    exit 1
  fi
fi

# 2. Auto-regenerate graphify/ if Python source changed
if git diff --cached --name-only | grep -qE '\.(py)$'; then
  if command -v python >/dev/null 2>&1 && [ -f "scripts/graphify.py" ]; then
    echo "Python source changed — regenerating graphify knowledge layer..."
    python scripts/graphify.py >/dev/null 2>&1 \
      && git add graphify/modules/ graphify/graph.json 2>/dev/null || true
  fi
fi

# 3. Refresh manifest cache (.ai/tree.txt etc.)
if [ -f ".ai/manifest.sh" ]; then
  bash .ai/manifest.sh >/dev/null 2>&1 || true
  git add .ai/tree.txt .ai/hotspots.txt .ai/recent-commits.txt 2>/dev/null || true
fi
