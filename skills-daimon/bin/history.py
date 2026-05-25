#!/usr/bin/env python3
"""
skills-daimon history store — counts only, no PII.

Tracks one tiny snapshot per (date, window_days) so the renderer can draw
trends. Same-day reruns overwrite (last write wins); the file never grows
beyond one entry per (date, window_days).

Every snapshot is passed through the shared redactor before writing as a
belt-and-braces — counts and labels only, but a string archetype could in
principle carry something funny.

Usage:
    python3 history.py append <  snapshot.json
    python3 history.py append snapshot.json
    python3 history.py read --last 6

Snapshot schema (input to append):
{
  "date": "2026-05-25", "window_days": 28,
  "sessions": 216, "labeled": 60,
  "scorecard": { "<key>": <numeric_value>, ... },   # numeric only
  "archetype": "The Builder-Scribe",                # optional, redacted
  "work_mix": { "dev": 60, "writing": 34, ... }     # optional
}

The on-disk file `~/.claude/skills/skills-daimon/history.jsonl` is JSON-Lines,
one entry per line, no headers.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Shared secret redactor.
sys.path.insert(0, str(Path(__file__).parent))
from redact import redact_in  # noqa: E402


HISTORY_PATH = Path.home() / ".claude" / "skills" / "skills-daimon" / "history.jsonl"


# Numeric-only fields we accept in scorecard. Strings drop silently so we
# never archive command text by accident.
_NUMERIC = (int, float)


def _scrub_snapshot(snap: dict) -> dict:
    """Belt-and-braces: drop non-numeric scorecard values; redact strings."""
    out = {
        "date": str(snap.get("date") or ""),
        "window_days": int(snap.get("window_days") or 0),
        "sessions": int(snap.get("sessions") or 0),
        "labeled": int(snap.get("labeled") or 0),
        "scorecard": {
            str(k): v for k, v in (snap.get("scorecard") or {}).items()
            if isinstance(v, _NUMERIC)
        },
        "archetype": str(snap.get("archetype") or "")[:60],
        "work_mix": {
            str(k): int(v) for k, v in (snap.get("work_mix") or {}).items()
            if isinstance(v, _NUMERIC)
        },
    }
    return redact_in(out)


def _load_entries() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    out = []
    try:
        for line in HISTORY_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out


def _atomic_write(entries: list[dict]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(e) for e in entries) + ("\n" if entries else "")
    fd, tmp = tempfile.mkstemp(prefix="history-", suffix=".jsonl",
                                dir=str(HISTORY_PATH.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.replace(tmp, HISTORY_PATH)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append(snap: dict) -> dict:
    """Insert / replace by (date, window_days). Returns the stored entry."""
    snap = _scrub_snapshot(snap)
    key = (snap["date"], snap["window_days"])
    if not snap["date"]:
        raise ValueError("snapshot missing date")
    entries = _load_entries()
    entries = [e for e in entries if (e.get("date"), e.get("window_days")) != key]
    entries.append(snap)
    # Stable ordering by date asc, window asc.
    entries.sort(key=lambda e: (e.get("date", ""), int(e.get("window_days") or 0)))
    _atomic_write(entries)
    return snap


def read_last(n: int, window_days: int | None = None) -> list[dict]:
    """Return up to n distinct entries (latest by date). Optional window filter."""
    entries = _load_entries()
    if window_days is not None:
        entries = [e for e in entries if int(e.get("window_days") or 0) == window_days]
    # entries are already sorted by date asc; take the tail
    return entries[-int(n):]


def _cmd_append(args) -> int:
    if args.path:
        snap = json.loads(Path(args.path).read_text())
    else:
        snap = json.loads(sys.stdin.read())
    stored = append(snap)
    print(json.dumps({"stored": stored}))
    return 0


def _cmd_read(args) -> int:
    out = read_last(args.last, args.window_days)
    print(json.dumps(out))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="skills-daimon history (counts only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_a = sub.add_parser("append", help="upsert one snapshot")
    ap_a.add_argument("path", nargs="?", default=None,
                      help="snapshot.json (default: stdin)")
    ap_a.set_defaults(func=_cmd_append)

    ap_r = sub.add_parser("read", help="read latest snapshots")
    ap_r.add_argument("--last", type=int, default=6)
    ap_r.add_argument("--window-days", dest="window_days", type=int, default=None)
    ap_r.set_defaults(func=_cmd_read)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
