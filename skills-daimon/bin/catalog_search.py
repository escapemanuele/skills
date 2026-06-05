#!/usr/bin/env python3
"""
skills-daimon catalog search.

Given job query-terms + the catalogs the scanner discovered, return VERIFIED
recommendation candidates — never invented ones. A candidate exists only if a
real catalog returned it; this module has no path that fabricates a name, URL,
or install command.

Handles:
  - marketplace JSON catalogs        (offline; reads the local index)
  - cli-provider catalogs            (`wp context <provider> search ...`)
  - skills.sh registry               (`npx skills find <term>`)
  - mcp-server catalogs              (CANNOT probe from a subprocess — emitted
                                      as `needs_live_probe` for the Claude session)

Then: dedupe by name (skills.sh > cli-provider > marketplace), drop anything
already installed or ignored, and attach source_url + install commands only when
the catalog actually provided what's needed to build them.

Usage:
    python3 catalog_search.py --scan scan.json --terms "pull request review" "linear triage"
    python3 catalog_search.py --scan scan.json --terms "pr review"   # JSON to stdout

Output JSON:
    {"candidates": [ {name, type, description, source_url, install:[...],
                      catalog, matched_terms:[...], installs?, stars?} ],
     "needs_live_probe": [ {name, type, probe} ],   # mcp-server catalogs
     "errors": [ "..." ]}
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Source priority for dedupe (higher wins).
_SOURCE_RANK = {"cli-registry": 3, "cli-provider": 2, "marketplace": 1}


def _norm(s) -> str:
    return str(s or "").lower()


def _matches(term: str, *fields) -> bool:
    """Case-insensitive match of `term` against the combined fields. Matches when
    the whole phrase is a substring, OR every significant word (len>=3) of the
    term appears somewhere (AND, not OR — keeps candidate sets precise). Empty
    term never matches."""
    t = _norm(term).strip()
    if not t:
        return False
    hay = " ".join(_norm(f) for f in fields)
    if t in hay:
        return True
    words = [w for w in t.split() if len(w) >= 3]
    return bool(words) and all(w in hay for w in words)


# --------------------------------------------------------------------------
# Marketplace (offline, pure — unit-testable without subprocess)
# --------------------------------------------------------------------------
def search_marketplace(marketplace_json: str, mp_name: str, terms: list[str]) -> list[dict]:
    try:
        data = json.loads(Path(marketplace_json).read_text())
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for p in data.get("plugins") or []:
        name = p.get("name")
        if not name:
            continue
        desc = p.get("description") or ""
        kw = " ".join(p.get("keywords") or []) if isinstance(p.get("keywords"), list) else ""
        matched = [t for t in terms if _matches(t, name, desc, kw)]
        if not matched:
            continue
        source = p.get("source")
        src = p.get("homepage") or (source.get("url") if isinstance(source, dict) else source) or ""
        out.append({
            "name": name,
            "type": p.get("type") or "plugin",
            "description": desc,
            "source_url": src,
            "install": [f"/plugin install {name}@{mp_name}"],
            "catalog": mp_name,
            "catalog_type": "marketplace",
            "matched_terms": matched,
        })
    return out


# --------------------------------------------------------------------------
# CLI-provider (wp context <provider> search) — subprocess, graceful
# --------------------------------------------------------------------------
def search_cli_provider(tool: str, catalog_name: str, terms: list[str],
                        errors: list[str], timeout: int = 30) -> list[dict]:
    if not shutil.which("wp"):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for term in terms:
        cmd = tool.split() + ["search", f"query={term}", "limit=10"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                continue
            hits = json.loads(r.stdout)
        except (subprocess.SubprocessError, ValueError, OSError) as e:
            errors.append(f"{catalog_name}: {e}")
            continue
        if isinstance(hits, dict):
            hits = hits.get("results") or hits.get("items") or []
        for h in hits if isinstance(hits, list) else []:
            name = h.get("name") or h.get("slug")
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({
                "name": name,
                "type": h.get("type") or "skill",
                "description": h.get("description") or "",
                "source_url": h.get("source_url") or "",
                "install": ([f"# verify, then install from {h.get('source_url')}"]
                            if h.get("source_url") else []),
                "catalog": catalog_name,
                "catalog_type": "cli-provider",
                "matched_terms": [term],
                "slug": h.get("slug"),
                "repo_key": h.get("repo_key"),
            })
    return out


# --------------------------------------------------------------------------
# skills.sh registry (npx skills find) — best-effort parse
# --------------------------------------------------------------------------
def search_skills_sh(terms: list[str], errors: list[str], timeout: int = 60) -> list[dict]:
    if not shutil.which("npx"):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for term in terms:
        try:
            r = subprocess.run(["npx", "skills", "find", term, "--json"],
                               capture_output=True, text=True, timeout=timeout)
            payload = r.stdout.strip()
            hits = json.loads(payload) if payload.startswith(("[", "{")) else None
        except (subprocess.SubprocessError, ValueError, OSError) as e:
            errors.append(f"skills.sh: {e}")
            continue
        if hits is None:
            # CLI returned human text, not JSON — leave to the live session
            # rather than risk a mis-parse that could invent a name.
            errors.append("skills.sh: non-JSON output; query live via `npx skills find`")
            continue
        if isinstance(hits, dict):
            hits = hits.get("results") or hits.get("skills") or []
        for h in hits if isinstance(hits, list) else []:
            name = h.get("name") or h.get("skill")
            owner = h.get("owner") or h.get("repo") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            install = []
            if owner and name:
                install = [f"npx skills add {owner}@{name} -g -y"]
            out.append({
                "name": name,
                "type": h.get("type") or "skill",
                "description": h.get("description") or "",
                "source_url": h.get("url") or h.get("source_url") or "",
                "install": install,
                "catalog": "skills.sh",
                "catalog_type": "cli-registry",
                "matched_terms": [term],
                "installs": h.get("installs") or h.get("install_count"),
                "stars": h.get("stars"),
            })
    return out


# --------------------------------------------------------------------------
# Dedupe + filter (pure — unit-testable)
# --------------------------------------------------------------------------
def dedupe_and_filter(hits: list[dict], installed: set[str], ignored: set[str]) -> list[dict]:
    """Drop installed/ignored names; dedupe by name keeping the highest-ranked
    source. Merges matched_terms across duplicates. Never adds a hit."""
    drop = {_norm(x) for x in (installed | ignored)}
    best: dict[str, dict] = {}
    for h in hits:
        name = h.get("name")
        if not name or _norm(name) in drop:
            continue
        key = _norm(name)
        rank = _SOURCE_RANK.get(h.get("catalog_type"), 0)
        if key not in best:
            best[key] = dict(h)
        else:
            cur = best[key]
            # merge matched terms
            merged = sorted(set(cur.get("matched_terms", [])) | set(h.get("matched_terms", [])))
            if rank > _SOURCE_RANK.get(cur.get("catalog_type"), 0):
                best[key] = dict(h)
            best[key]["matched_terms"] = merged
    return sorted(best.values(), key=lambda h: (-len(h.get("matched_terms", [])), _norm(h.get("name"))))


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def search(scan: dict, terms: list[str]) -> dict:
    catalogs = scan.get("available_catalogs") or []
    installed = set(scan.get("installed_skills") or []) | set(scan.get("installed_plugins") or [])
    ignored = set(scan.get("ignored_names") or [])
    errors: list[str] = []
    needs_live_probe: list[dict] = []
    raw: list[dict] = []

    for c in catalogs:
        ctype = c.get("type")
        if ctype == "marketplace":
            raw += search_marketplace(c.get("marketplace_json", ""), c.get("name", ""), terms)
        elif ctype == "cli-provider":
            raw += search_cli_provider(c.get("tool", ""), c.get("name", ""), terms, errors)
        elif ctype == "cli-registry":
            raw += search_skills_sh(terms, errors)
        elif ctype == "mcp-server":
            needs_live_probe.append({"name": c.get("name"), "type": "mcp-server", "probe": c.get("probe")})

    candidates = dedupe_and_filter(raw, installed, ignored)
    return {"candidates": candidates, "needs_live_probe": needs_live_probe, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser(description="skills-daimon catalog search (verified candidates only)")
    ap.add_argument("--scan", required=True, help="scan.py JSON path")
    ap.add_argument("--terms", nargs="+", required=True, help="job query terms")
    args = ap.parse_args()
    scan = json.loads(Path(args.scan).read_text())
    out = search(scan, args.terms)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
