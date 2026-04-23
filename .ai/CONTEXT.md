# Live Context (overwritten at every session end)

## Current Task
.ai/ session layer setup — initial infrastructure commit

## State
- Phase: complete
- Files recently touched: .ai/MEMORY.md, .ai/CONTEXT.md, .ai/CHANGELOG-AI.md,
  .ai/DECISIONS.md, .ai/deps.md, .ai/manifest.sh, .ai/switch-guard.sh,
  .aiignore, CLAUDE.md, AGENTS.md, .github/copilot-instructions.md,
  scripts/pre-commit-hook.sh
- Last command run: bash .ai/manifest.sh
- Last test result: n/a (infrastructure-only changes)

## Mental Model
- .ai/ is the session layer. graphify/ is the knowledge layer. Read both at session start.
- MEMORY.md = invariants (read once, rarely changes)
- CONTEXT.md = this file (overwrite each session end)
- CHANGELOG-AI.md = append-only history
- manifest.sh pre-computes expensive find/ls/git-log so tools don't re-run them

## Next Action
First real task — user directs. Check tasks/todo.md for backlog.

## Do Not
- Do not install graphifyy pip package — project uses custom scripts/graphify.py
- Do not modify .claude/agents/ files unless doing agent-domain work
- Do not use DynamoDB scan() — always use GSI query()
