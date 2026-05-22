#!/usr/bin/env python3
"""
skills-daimon crest generator (optional, network + cost).

Generates a small heraldic "crest" image for the user's archetype title via the
OpenAI image API, and saves it next to the HTML report. Stdlib only.

PRIVACY / COST — read before enabling:
- This is the ONLY part of skills-daimon that leaves the machine. Everything
  else is local. It runs only when OPENAI_API_KEY is set in the environment.
- The prompt sent contains ONLY the archetype title (e.g. "The Builder-Scribe")
  and a fixed style string. No session data, no file paths, no counts.
- Each run costs money (gpt-image-1, low quality ≈ $0.01–0.02/image). Since the
  skill generates on every run when the key is present, that adds up.
- No key set → prints {"skipped": "no OPENAI_API_KEY"} and exits 0. The report
  is unaffected.

Usage:
    python3 generate_crest.py "The Builder-Scribe" [--date YYYY-MM-DD]

Prints JSON: {"path": "...png"} on success, or {"skipped": "..."} / {"error": "..."}.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.openai.com/v1/images/generations"
MODEL = "gpt-image-1"
QUALITY = "low"   # cheapest tier
SIZE = "1024x1024"
TIMEOUT = 60


def build_prompt(title: str) -> str:
    """Prompt uses ONLY the archetype title + a fixed style. No user data."""
    title = title.strip() or "The Coder"
    return (
        f"A minimalist heraldic emblem / crest representing the archetype "
        f"\"{title}\". Flat vector style, bold simple shapes, limited palette of "
        f"indigo and violet on a soft cream background, centered, no text, no "
        f"lettering. Clean, modern, badge-like."
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("title", help="Archetype title (the only thing sent to the API).")
    ap.add_argument("--date", default=None, help="Date stamp for the filename.")
    args = ap.parse_args()

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print(json.dumps({"skipped": "no OPENAI_API_KEY"}))
        return 0

    body = json.dumps({
        "model": MODEL,
        "prompt": build_prompt(args.title),
        "n": 1,
        "size": SIZE,
        "quality": QUALITY,
    }).encode()

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="ignore")[:200]
        print(json.dumps({"error": f"HTTP {e.code}: {detail}"}))
        return 0  # never break the report
    except Exception as e:  # network/timeout/etc.
        print(json.dumps({"error": str(e)[:200]}))
        return 0

    try:
        b64 = data["data"][0]["b64_json"]
        img = base64.b64decode(b64)
    except Exception:
        print(json.dumps({"error": "unexpected API response shape"}))
        return 0

    out_dir = Path.home() / ".claude" / "skills" / "skills-daimon" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = args.date or "crest"
    out = out_dir / f"skills-daimon-crest-{date}.png"
    out.write_bytes(img)
    print(json.dumps({"path": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
