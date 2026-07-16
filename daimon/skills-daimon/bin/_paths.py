"""Where skills-daimon keeps its own data (history, reports, ignored list).

Resolution order:
  1. $SKILLS_DAIMON_HOME if set (explicit override).
  2. ~/.claude if it exists (Claude Code users — back-compat default).
  3. ~/.codex if it exists (Codex-only machines).
  4. ~/.claude otherwise.

This keeps a Codex-only install self-contained under ~/.codex instead of
spuriously creating ~/.claude. Dual-tool users default to ~/.claude; set
SKILLS_DAIMON_HOME=$HOME/.codex to keep Codex runs separate.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    env = os.environ.get("SKILLS_DAIMON_HOME")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if (home / ".claude").exists():
        return home / ".claude"
    if (home / ".codex").exists():
        return home / ".codex"
    return home / ".claude"


def skill_data_dir() -> Path:
    """The skills-daimon data directory under the resolved home."""
    return data_home() / "skills" / "skills-daimon"
