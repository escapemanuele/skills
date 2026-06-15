#!/usr/bin/env bash
# Install skills-daimon as an OpenAI Codex skill.
#
# Copies the bundled scripts + references into ~/.codex/skills/skills-daimon and
# drops the Codex-native SKILL.md in place. Idempotent — safe to re-run to
# upgrade. Requires python3 (and npx for the live registry query).
#
#   ./install-codex.sh            # install to $CODEX_HOME/skills (default ~/.codex)
#   CODEX_HOME=/custom ./install-codex.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CODEX_HOME:-$HOME/.codex}/skills/skills-daimon"

if [ ! -f "$SRC/SKILL.codex.md" ]; then
  echo "error: run this from the skills-daimon directory (SKILL.codex.md not found)" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

echo "Installing skills-daimon → $DEST"
mkdir -p "$DEST"

# Ship the runnable pieces only; skip Claude-specific SKILL.md, tests, caches.
cp -R "$SRC/bin" "$DEST/"
cp -R "$SRC/references" "$DEST/"
cp "$SRC/SKILL.codex.md" "$DEST/SKILL.md"
rm -rf "$DEST/bin/__pycache__"

if ! command -v npx >/dev/null 2>&1; then
  echo "note: npx not found — the live 'npx skills find' registry query will be skipped"
  echo "      (offline catalogs still work). Install Node.js to enable it."
fi

echo "Done. Restart Codex (or reload skills) and run:  skills-daimon"
echo "Or run directly:  python3 \"$DEST/bin/run.py\" --days 28 --source codex"
