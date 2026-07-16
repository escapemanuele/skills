#!/usr/bin/env python3
"""
Write a "what we learned" note to the configured store.

Input payload (JSON on stdin OR --payload <path>):
{
  "session_id": "<uuid>",
  "repo": "<short repo name>",
  "outcome": "<facet outcome>",
  "tags": ["..."],
  "date": "YYYY-MM-DD",                # optional, defaults to today
  "title": "Yarn cache stale on PR ...",  # required
  "figured_out": "The CI failure was ...",  # required
  "why_it_matters": "Wasted ~30 min ...",   # optional
  "try_next_time": "When the lint job ...", # optional
  "see_also": ["2026-04-12-yarn-cache"]      # optional Obsidian backlinks
}

The body content + frontmatter ALL pass through the shared redactor before
hitting disk. A `.skills-daimon.json` manifest in the store dir keeps a list
of notes we wrote so the user can audit/delete cleanly.

Output:
{
  "path": "/abs/path/to/2026-05-25-yarn-cache-stale.md",
  "slug": "yarn-cache-stale",
  "store": {...}
}
or
{
  "error": "..."
}

Usage:
    python3 save.py < payload.json
    python3 save.py --payload payload.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from redact import redact, redact_in  # noqa: E402
from store import (
    CONFIG_PATH, DEFAULT_STORE, MANIFEST_NAME, load as load_config,
    resolved_path,
)


SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str, maxlen: int = 48) -> str:
    """Lowercase kebab-slug from a title. Always returns at least 'note'."""
    s = (s or "").strip().lower()
    s = SLUG_RE.sub("-", s).strip("-")
    s = s[:maxlen].strip("-") or "note"
    return s


def _frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k in ("date", "repo", "session", "outcome"):
        v = meta.get(k)
        if v:
            lines.append(f"{k}: {v}")
    tags = meta.get("tags") or []
    if tags:
        lines.append("tags: [" + ", ".join(tags) + "]")
    lines.append("---")
    return "\n".join(lines)


def _wiki(see_also: list[str]) -> str:
    if not see_also:
        return ""
    links = " · ".join(f"[[{s}]]" for s in see_also)
    return f"\n\n*See also:* {links}\n"


def render_markdown(p: dict, cfg: dict | None) -> str:
    """Render the note body. Wiki-links only when kind == 'obsidian'."""
    meta = {
        "date": p.get("date"),
        "repo": p.get("repo"),
        "session": p.get("session_id"),
        "outcome": p.get("outcome"),
        "tags": p.get("tags") or [],
    }
    body = []
    body.append(_frontmatter(meta))
    body.append("")
    body.append(f"# {p.get('title','').strip()}")
    body.append("")
    body.append("## What we figured out")
    body.append((p.get("figured_out") or "").strip())
    why = (p.get("why_it_matters") or "").strip()
    if why:
        body.append("")
        body.append("## Why it matters")
        body.append(why)
    try_next = (p.get("try_next_time") or "").strip()
    if try_next:
        body.append("")
        body.append("## Try this next time")
        body.append(try_next)
    if cfg and cfg.get("kind") == "obsidian":
        sa = p.get("see_also") or []
        if sa:
            body.append(_wiki(sa))
    body.append("")
    return "\n".join(body)


def _update_manifest(store_dir: Path, entry: dict) -> None:
    mf = store_dir / MANIFEST_NAME
    try:
        data = json.loads(mf.read_text()) if mf.is_file() else {"notes": []}
    except ValueError:
        data = {"notes": []}
    notes = data.get("notes") or []
    notes.append(entry)
    data["notes"] = notes
    mf.write_text(json.dumps(data, indent=2) + "\n")


def save(payload: dict) -> dict:
    cfg = load_config()
    store = resolved_path(cfg)
    store.mkdir(parents=True, exist_ok=True)

    date = payload.get("date") or _dt.date.today().isoformat()
    title = (payload.get("title") or "").strip()
    if not title or not (payload.get("figured_out") or "").strip():
        return {"error": "payload requires `title` and `figured_out`"}
    slug = slugify(title)
    fname = f"{date}-{slug}.md"
    out = store / fname

    payload = dict(payload)  # don't mutate caller's copy
    payload["date"] = date

    # Redact the entire payload first.
    payload = redact_in(payload)

    md = render_markdown(payload, cfg)
    md = redact(md)  # belt-and-braces on the assembled text

    out.write_text(md, encoding="utf-8")
    _update_manifest(store, {
        "date": date,
        "slug": slug,
        "file": fname,
        "session_id": payload.get("session_id"),
        "repo": payload.get("repo"),
    })

    return {
        "path": str(out),
        "slug": slug,
        "store": {"kind": (cfg or {}).get("kind", "default"), "dir": str(store)},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=None,
                    help="JSON file path (default: read stdin)")
    args = ap.parse_args()

    raw = Path(args.payload).read_text() if args.payload else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError as e:
        print(json.dumps({"error": f"invalid JSON: {e}"}))
        return 1
    result = save(payload)
    print(json.dumps(result, indent=2))
    return 0 if "path" in result else 1


if __name__ == "__main__":
    sys.exit(main())
