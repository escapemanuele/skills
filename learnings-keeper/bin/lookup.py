#!/usr/bin/env python3
"""
Search the learnings store for past notes that match a query.

MVP: shells out to `rg` (ripgrep). If `rg` isn't available, falls back to a
plain glob+grep so it never fails outright.

Output:
{
  "store": "/abs/path/to/store",
  "query": "<query>",
  "hits": [
    {
      "path": "/abs/.../2026-04-12-yarn-cache-stale.md",
      "title": "Yarn cache stale on PR ...",
      "date": "2026-04-12",
      "repo": "wp-calypso",
      "snippet": "first line of the matched paragraph"
    }
  ]
}

Usage:
    python3 lookup.py --query "yarn cache"
    python3 lookup.py --query "yarn cache" --repo wp-calypso --limit 5
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from store import load as load_config, resolved_path  # noqa: E402


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _parse_meta(text: str) -> dict:
    out = {}
    m = FRONTMATTER_RE.match(text)
    if not m:
        return out
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip().lower()] = v.strip()
    return out


def _title(text: str) -> str:
    m = TITLE_RE.search(text)
    return m.group(1).strip() if m else ""


def _first_match_snippet(text: str, query: str, span: int = 140) -> str:
    if not query:
        return ""
    m = re.search(re.escape(query), text, re.IGNORECASE)
    if not m:
        return ""
    start = max(0, m.start() - span // 2)
    end = min(len(text), m.end() + span // 2)
    return text[start:end].replace("\n", " ").strip()


def lookup(query: str, repo: str | None, limit: int) -> dict:
    cfg = load_config()
    store = resolved_path(cfg)
    if not store.is_dir():
        return {"store": str(store), "query": query, "hits": [],
                 "note": "store does not exist yet"}

    # rg first (fast); fallback to glob if missing
    files: list[str] = []
    if shutil.which("rg"):
        try:
            r = subprocess.run(
                ["rg", "-l", "-i", "--no-messages", query, str(store), "-g", "*.md"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                files = [ln for ln in r.stdout.splitlines() if ln.strip()]
        except Exception:
            files = []
    if not files:
        # Fallback
        q = query.lower()
        for p in store.rglob("*.md"):
            try:
                if q in p.read_text(errors="ignore").lower():
                    files.append(str(p))
            except OSError:
                continue

    hits = []
    for f in files:
        p = Path(f)
        try:
            t = p.read_text(errors="ignore")
        except OSError:
            continue
        meta = _parse_meta(t)
        if repo and meta.get("repo", "").lower() != repo.lower():
            continue
        hits.append({
            "path": str(p),
            "title": _title(t) or p.stem,
            "date": meta.get("date", ""),
            "repo": meta.get("repo", ""),
            "snippet": _first_match_snippet(t, query),
        })
    # newest first if date present
    hits.sort(key=lambda h: h["date"], reverse=True)
    return {"store": str(store), "query": query, "hits": hits[:int(limit)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    print(json.dumps(lookup(args.query, args.repo, args.limit), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
