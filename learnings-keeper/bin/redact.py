#!/usr/bin/env python3
"""
Shared secret redactor for skills-daimon.

Every disk artifact (scan summary, HTML report, history.jsonl, learning notes)
goes through `redact(text)` before being written. The goal is **never** write a
plausible secret to disk, even transiently. We accept false positives — the
masked token is still readable enough for coaching.

Patterns covered:
- Authorization / Bearer headers
- OpenAI-style keys (`sk-...`)
- GitHub tokens (`ghp_…`, `gho_…`, `ghu_…`, `ghs_…`, `ghr_…`)
- AWS access key IDs (`AKIA…`, `ASIA…`)
- Generic 32+ hex strings
- Basic-auth in URLs (`https://user:pass@host`)
- `password=` / `token=` query/key forms

Anything that matches is replaced with `<REDACTED>` (or a typed variant for
clarity, e.g. `<REDACTED:bearer>`).
"""

from __future__ import annotations

import re

_PATTERNS = [
    # Authorization / Bearer headers — consume value up to quote/newline so
    # `Authorization: Bearer <token>` doesn't leave the token exposed after
    # eating only "Bearer".
    (re.compile(r"(?i)\b(Authorization|X-Api-Key|X-Auth-Token)\s*[:=]\s*[^'\"\n]+"),
     r"\1: <REDACTED>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer <REDACTED>"),

    # Vendor tokens — keep the prefix, mask the body
    (re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}"), "sk-<REDACTED>"),
    (re.compile(r"\bgh[opsur]_[A-Za-z0-9]{20,}"), "<REDACTED:gh-token>"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "<REDACTED:aws-key>"),

    # Basic-auth in URLs: https://user:pass@host -> https://<REDACTED>@host
    (re.compile(r"(\bhttps?://)[^\s/@:]+:[^\s/@]+@"), r"\1<REDACTED>@"),

    # password=... / token=... / api_key=...  (query strings, env, ini lines)
    (re.compile(r"(?i)\b(password|passwd|token|api[_-]?key|secret)\s*[:=]\s*\S+"),
     r"\1=<REDACTED>"),
]

# 32+ hex / base64-ish (kept last to avoid clobbering above forms)
_GENERIC_LONG = re.compile(r"\b[A-Fa-f0-9]{32,}\b")


def redact(text: str) -> str:
    """Mask plausible secrets in `text`. Idempotent. Always returns a string."""
    if not text or not isinstance(text, str):
        return text or ""
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    out = _GENERIC_LONG.sub("<REDACTED:hex>", out)
    return out


def redact_in(obj):
    """Walk a JSON-ish structure and redact every string leaf in-place style.

    Returns a new structure with strings passed through `redact()`. Dicts/lists
    are rebuilt; other values pass through unchanged.
    """
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_in(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_in(v) for v in obj]
    return obj


if __name__ == "__main__":  # tiny smoke test
    import sys
    samples = [
        "curl -H 'Authorization: Bearer abcdef1234567890.abcdef' https://x",
        "export OPENAI_API_KEY=sk-proj-AAAAAAAAAAAAAAAAAAAAAAA",
        "gh auth login --with-token ghp_abcdefghijklmnopqrstuvwxyz0123456",
        "aws sts AKIAIOSFODNN7EXAMPLE",
        "psql https://u:p@host/db",
        "password=hunter2 token=tok_xyzzy123",
        "deadbeef0123456789abcdef0123456789abcdef",
        "normal text, no secrets",
    ]
    for s in samples:
        print(redact(s))
    # also test redact_in on a nested structure
    print(redact_in({"k": ["sk-AAAAAAAAAAAAAAAAAAAA", {"v": "ok"}]}))
    sys.exit(0)
