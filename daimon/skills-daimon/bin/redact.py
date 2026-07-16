#!/usr/bin/env python3
"""
Shared secret redactor for skills-daimon.

Every disk artifact (scan summary, HTML report, history.jsonl, learning notes)
goes through `redact(text)` before being written. The goal is **never** write a
plausible secret to disk, even transiently. We accept false positives — the
masked token is still readable enough for coaching.

Patterns covered:
- Private key blocks (`-----BEGIN ... PRIVATE KEY-----` … `-----END ...`)
- Authorization / Bearer headers
- OpenAI-style keys (`sk-...`)
- GitHub tokens (`ghp_…`, `gho_…`, `ghu_…`, `ghs_…`, `ghr_…`)
- GitLab personal access tokens (`glpat-…`)
- npm tokens (`npm_…`)
- Slack tokens (`xoxb-`, `xoxp-`, `xoxa-`, `xoxr-`, `xoxs-`, `xapp-`)
- Google API keys (`AIza…`)
- JWT-like tokens (`eyJ….….…`)
- AWS access key IDs (`AKIA…`, `ASIA…`)
- Generic 32+ hex strings and long base64-ish secrets
- Basic-auth in URLs (`https://user:pass@host`)
- `password=` / `token=` query/key forms

Anything that matches is replaced with `<REDACTED>` (or a typed variant for
clarity, e.g. `<REDACTED:bearer>`). `redact_in` also masks dictionary KEYS as
defense in depth, not just values.
"""

from __future__ import annotations

import re

_PATTERNS = [
    # Private key blocks (multiline) — mask the entire PEM body first.
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
     "<REDACTED:private-key>"),

    # Authorization / Bearer headers — consume value up to quote/newline so
    # `Authorization: Bearer <token>` doesn't leave the token exposed after
    # eating only "Bearer".
    (re.compile(r"(?i)\b(Authorization|X-Api-Key|X-Auth-Token)\s*[:=]\s*[^'\"\n]+"),
     r"\1: <REDACTED>"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer <REDACTED>"),

    # JWT-like tokens: header.payload(.signature), all base64url, starting eyJ
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}(?:\.[A-Za-z0-9_\-]+){1,2}"), "<REDACTED:jwt>"),

    # Vendor tokens — keep the prefix where useful, mask the body
    (re.compile(r"\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}"), "sk-<REDACTED>"),
    (re.compile(r"\bgh[opsur]_[A-Za-z0-9]{20,}"), "<REDACTED:gh-token>"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"), "<REDACTED:gitlab-token>"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}"), "<REDACTED:npm-token>"),
    (re.compile(r"\b(?:xox[baprs]|xapp)-[A-Za-z0-9-]{10,}"), "<REDACTED:slack-token>"),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}"), "<REDACTED:google-key>"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "<REDACTED:aws-key>"),

    # Basic-auth in URLs: https://user:pass@host -> https://<REDACTED>@host
    (re.compile(r"(\bhttps?://)[^\s/@:]+:[^\s/@]+@"), r"\1<REDACTED>@"),

    # password=... / token=... / api_key=...  (query strings, env, ini lines)
    (re.compile(r"(?i)\b(password|passwd|token|api[_-]?key|secret)\s*[:=]\s*\S+"),
     r"\1=<REDACTED>"),
]

# 32+ hex (kept late to avoid clobbering above forms)
_GENERIC_LONG = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
# Long base64-ish secrets: ≥40 chars from the base64 alphabet containing BOTH a
# digit and a letter (so ordinary long words / paths don't trip it).
_BASE64_LONG = re.compile(
    r"\b(?=[A-Za-z0-9+/]*\d)(?=[A-Za-z0-9+/]*[A-Za-z])[A-Za-z0-9+/]{40,}={0,2}\b"
)


def redact(text: str) -> str:
    """Mask plausible secrets in `text`. Idempotent. Always returns a string."""
    if not text or not isinstance(text, str):
        return text or ""
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    out = _GENERIC_LONG.sub("<REDACTED:hex>", out)
    out = _BASE64_LONG.sub("<REDACTED:b64>", out)
    return out


def redact_in(obj):
    """Walk a JSON-ish structure and redact every string leaf in-place style.

    Returns a new structure with strings passed through `redact()`. Dicts/lists
    are rebuilt; other values pass through unchanged.
    """
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        # Redact keys too (defense in depth) — a raw_http_hosts host or similar
        # could in principle carry a secret. Non-string keys pass through.
        return {(redact(k) if isinstance(k, str) else k): redact_in(v)
                for k, v in obj.items()}
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
