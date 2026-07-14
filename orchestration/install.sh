#!/usr/bin/env bash
# Install (or update) the agent-orchestration system into ~/.claude.
# The repo is the source of truth: edit here, then re-run this script.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${CLAUDE_DIR:-$HOME/.claude}"

mkdir -p "$DEST/agents" "$DEST/orchestration"

cp "$SRC"/agents/*.md "$DEST/agents/"

for skill in feature bug review; do
  mkdir -p "$DEST/skills/$skill"
  cp "$SRC/skills/$skill/SKILL.md" "$DEST/skills/$skill/"
done

cp "$SRC/MODEL-POLICY.md" "$SRC/EVALS.md" "$SRC/README.md" "$DEST/orchestration/"

echo "Orchestration system installed to $DEST"
echo "Agents:  code-explorer, implementer, reviewer, test-runner"
echo "Skills:  /feature, /bug, /review"
