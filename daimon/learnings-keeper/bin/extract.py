#!/usr/bin/env python3
"""
Extract candidate "what we learned" snippets from a Claude Code session.

Reads:
- ~/.claude/projects/<encoded-cwd>/<session>.jsonl
- ~/.claude/usage-data/facets/<session>.json     (if present)

Emits JSON:
{
  "session_id": "...",
  "cwd": "/Users/.../repo",
  "repo": "<basename>",
  "outcome": "fully_achieved|mostly_achieved|partially_achieved|not_achieved|unclear_from_transcript|unknown",
  "primary_success": "...|null",
  "candidates": [
    { "text": "...", "source": "user|assistant" }
  ],
  "files_touched": [ "<relative path>", ... ],
  "tags": [ "<repo>", "<kind hint>", ... ],
  "skipped": "outcome_negative"   # only when refusing to extract
}

Refuses to extract when `outcome ∈ {not_achieved, unclear_from_transcript}`
unless `--insist` is passed.

All text output passes through the shared redactor.

Usage:
    python3 extract.py --session /path/to/<sid>.jsonl
    python3 extract.py --latest                       # most recently touched session
    python3 extract.py --latest --insist              # extract anyway
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from redact import redact, redact_in  # noqa: E402


PROJECTS_DIR = Path.home() / ".claude" / "projects"
FACETS_DIR = Path.home() / ".claude" / "usage-data" / "facets"

# Sentence-fragment patterns that indicate a "we figured it out" moment.
# Multilingual nods (Italian/Spanish/French) included since journals often live there.
LEARN_PATTERNS = [
    r"\bwe decided\b",
    r"\bthe (?:fix|bug|issue|problem|root\s*cause) was\b",
    r"\bturns out\b",
    r"\bit turns out\b",
    r"\bturned out (?:to be|that)\b",
    r"\b(?:i\s+)?figured (?:it )?out\b",
    r"\b(?:we|i)\s+learned (?:that|how)\b",
    r"\bsolved (?:it|the)\b",
    r"\bgotcha:\b",
    r"\bTIL\b",
    r"\bso the trick is\b",
    r"\bthe trick was\b",
    r"\bthe takeaway is\b",
    r"\bkey insight:\b",
    r"\b(?:abbiamo )?capito\b",     # IT
    r"\bla soluzione era\b",         # IT
    r"\bla causa era\b",              # IT
    r"\bla solución era\b",          # ES
    r"\ble bug venait de\b",         # FR
]
LEARN_RE = re.compile("|".join(LEARN_PATTERNS), re.IGNORECASE)

# Cut a sentence around the matched phrase (keep ±200 chars, on sentence boundaries
# where possible). Sentences are split very loosely so multilingual journal text
# still slices reasonably.
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'`(\[À-Ý])")

MAX_CANDIDATES = 8       # before user-side dedupe/triage
SAMPLE_MAX_CHARS = 320   # per candidate cap


def latest_session() -> Path | None:
    if not PROJECTS_DIR.is_dir():
        return None
    files = list(PROJECTS_DIR.glob("*/*.jsonl"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def text_of_content(content) -> str:
    """Pull the text portion out of a Claude Code event's `message.content`."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "\n".join(parts)
    return ""


def is_caveat_or_system(text: str) -> bool:
    if not text:
        return True
    head = text.lstrip()[:200]
    return head.startswith((
        "<local-command-caveat>", "<local-command-stdout>",
        "<command-name>", "<command-message>", "<command-args>",
        "<system-reminder>", "<bash-stdout>", "<bash-stderr>",
        "[Request interrupted", "Caveat: The messages",
    ))


def slice_around(text: str, m: re.Match, span: int = 200) -> str:
    """Take a chunk around the match; prefer sentence boundaries."""
    start = max(0, m.start() - span)
    end = min(len(text), m.end() + span)
    chunk = text[start:end]
    sents = SENT_SPLIT.split(chunk)
    # Try to find which sentence contains the original phrase, return ±1.
    target = m.group(0).lower()
    for i, s in enumerate(sents):
        if target in s.lower():
            lo = max(0, i - 1)
            hi = min(len(sents), i + 2)
            return " ".join(sents[lo:hi]).strip()
    return chunk.strip()


def extract_candidates(jsonl_path: Path) -> tuple[list[dict], list[str], str | None]:
    """Return (candidates, files_touched, cwd) from a session jsonl."""
    candidates: list[dict] = []
    seen_keys: set[str] = set()
    files_touched: list[str] = []
    files_seen: set[str] = set()
    cwd_val: str | None = None

    try:
        fh = jsonl_path.open()
    except OSError:
        return [], [], None

    with fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if cwd_val is None:
                c = ev.get("cwd")
                if isinstance(c, str) and c:
                    cwd_val = c
            typ = ev.get("type")
            if typ in ("user", "assistant"):
                msg = ev.get("message") or {}
                text = text_of_content(msg.get("content"))
                if is_caveat_or_system(text) or not text:
                    pass
                else:
                    for m in LEARN_RE.finditer(text):
                        chunk = slice_around(text, m, span=200)[:SAMPLE_MAX_CHARS]
                        key = chunk[:80].lower()
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        candidates.append({"text": chunk.strip(), "source": typ})
                        if len(candidates) >= MAX_CANDIDATES * 2:
                            break
            if typ == "assistant":
                msg = ev.get("message") or {}
                for block in (msg.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if block.get("name") in ("Edit", "Write", "NotebookEdit"):
                            inp = block.get("input") or {}
                            fp = inp.get("file_path") or inp.get("notebook_path") or ""
                            if fp and fp not in files_seen:
                                files_seen.add(fp)
                                files_touched.append(fp)

    # Keep the strongest few; in MVP that's just the first MAX_CANDIDATES.
    return candidates[:MAX_CANDIDATES], files_touched, cwd_val


def load_facet(session_id: str) -> dict | None:
    try:
        return json.loads((FACETS_DIR / f"{session_id}.json").read_text())
    except (OSError, ValueError):
        return None


def derive_tags(repo: str | None, files: list[str], outcome: str) -> list[str]:
    tags = []
    if repo:
        tags.append(repo.lower())
    exts = {f.rsplit(".", 1)[-1].lower() for f in files if "." in f}
    for hint, tag in (
        ("ts", "typescript"), ("tsx", "typescript"), ("js", "javascript"),
        ("py", "python"), ("php", "php"), ("rs", "rust"), ("go", "go"),
        ("md", "writing"), ("yml", "ci"), ("yaml", "ci"),
    ):
        if hint in exts:
            tags.append(tag)
    if outcome and outcome not in ("unknown",):
        tags.append(outcome)
    # Dedupe, preserve order
    seen = set()
    uniq = []
    for t in tags:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq[:6]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", help="absolute path to a session jsonl")
    g.add_argument("--latest", action="store_true",
                   help="most recently modified session under ~/.claude/projects/")
    ap.add_argument("--insist", action="store_true",
                    help="extract even for not_achieved / unclear outcomes")
    args = ap.parse_args()

    if args.latest:
        path = latest_session()
        if not path:
            print(json.dumps({"error": "no session jsonl found"}))
            return 1
    else:
        path = Path(args.session).expanduser()
        if not path.is_file():
            print(json.dumps({"error": f"session not found: {path}"}))
            return 1

    session_id = path.stem
    facet = load_facet(session_id)
    outcome = (facet or {}).get("outcome") or "unknown"
    primary = (facet or {}).get("primary_success") or None
    if outcome in ("not_achieved", "unclear_from_transcript") and not args.insist:
        print(json.dumps({"skipped": "outcome_negative", "outcome": outcome,
                          "session_id": session_id}))
        return 0

    candidates, files, cwd_val = extract_candidates(path)
    repo = Path(cwd_val).name if cwd_val else None
    tags = derive_tags(repo, files, outcome)

    out = {
        "session_id": session_id,
        "cwd": cwd_val,
        "repo": repo,
        "outcome": outcome,
        "primary_success": primary,
        "candidates": candidates,
        "files_touched": files[:20],
        "tags": tags,
    }
    # Redact every string before printing.
    out = redact_in(out)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
