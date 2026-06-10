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

Batch mode (one call for the whole Step-3 fan-out; jobs run in parallel).
Jobs are separated by `|`; within a job, commas separate search PHRASES
(phrases stay whole — better registry ranking than single words):
    python3 catalog_search.py --scan scan.json --jobs "git safety, commit push|sql query, data analysis" --top 6

Output JSON:
    {"candidates": [ {name, type, description, source_url, install:[...],
                      catalog, matched_terms:[...], installs?, stars?} ],
     "needs_live_probe": [ {name, type, probe} ],   # mcp-server catalogs
     "errors": [ "..." ]}
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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
# skills.sh registry (npx skills find) — JSON if offered, else strict text parse
# --------------------------------------------------------------------------
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# "owner/repo@skill  123 installs" / "owner/repo@skill  34.9K installs" —
# every captured token comes verbatim from the registry output, so nothing
# here can invent a name.
_SKILLS_SH_HIT_RE = re.compile(r"^(\S+/[^@\s]+)@(\S+(?: \S+)*?)\s+([\d.,]+[KkMm]?) installs?$")
_SKILLS_SH_URL_RE = re.compile(r"^└ (https://skills\.sh/\S+)$")


def _installs_to_int(s: str) -> int:
    s = s.replace(",", "")
    mult = {"k": 1_000, "m": 1_000_000}.get(s[-1].lower(), 1)
    if mult > 1:
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def parse_skills_sh_text(text: str, term: str) -> list[dict]:
    """Strictly parse `npx skills find` human output. A hit is accepted only
    when the name line matches the registry's exact shape; the URL is taken
    from the following `└ https://skills.sh/...` line when present."""
    out: list[dict] = []
    lines = [_ANSI_RE.sub("", ln).strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        m = _SKILLS_SH_HIT_RE.match(ln)
        if not m:
            continue
        repo, skill, installs = m.group(1), m.group(2), _installs_to_int(m.group(3))
        url = ""
        if i + 1 < len(lines):
            mu = _SKILLS_SH_URL_RE.match(lines[i + 1])
            if mu:
                url = mu.group(1)
        out.append({
            "name": skill,
            "type": "skill",
            "description": "",
            "source_url": url,
            "install": [f"npx skills add {repo}@{skill}"],
            "catalog": "skills.sh",
            "catalog_type": "cli-registry",
            "matched_terms": [term],
            "installs": installs,
        })
    return out


def _skills_sh_one_term(term: str, errors: list[str], timeout: int) -> list[dict]:
    try:
        r = subprocess.run(["npx", "skills", "find", term, "--json"],
                           capture_output=True, text=True, timeout=timeout)
        payload = r.stdout.strip()
    except (subprocess.SubprocessError, OSError) as e:
        errors.append(f"skills.sh: {e}")
        return []
    if payload.startswith(("[", "{")):
        try:
            hits = json.loads(payload)
        except ValueError:
            hits = None
        if isinstance(hits, dict):
            hits = hits.get("results") or hits.get("skills") or []
        if isinstance(hits, list):
            out = []
            for h in hits:
                name = h.get("name") or h.get("skill")
                owner = h.get("owner") or h.get("repo") or ""
                if not name:
                    continue
                out.append({
                    "name": name,
                    "type": h.get("type") or "skill",
                    "description": h.get("description") or "",
                    "source_url": h.get("url") or h.get("source_url") or "",
                    "install": [f"npx skills add {owner}@{name} -g -y"] if owner else [],
                    "catalog": "skills.sh",
                    "catalog_type": "cli-registry",
                    "matched_terms": [term],
                    "installs": h.get("installs") or h.get("install_count"),
                    "stars": h.get("stars"),
                })
            return out
    # Human text — parse strictly (registry-verbatim tokens only).
    return parse_skills_sh_text(payload, term)


def search_skills_sh(terms: list[str], errors: list[str], timeout: int = 60) -> list[dict]:
    if not shutil.which("npx"):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    # One npx process per term is network-bound — run them concurrently.
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(terms)))) as ex:
        for hits in ex.map(lambda t: _skills_sh_one_term(t, errors, timeout), terms):
            for h in hits:
                if h["name"] in seen:
                    continue
                seen.add(h["name"])
                out.append(h)
    return out


# --------------------------------------------------------------------------
# Dedupe + filter (pure — unit-testable)
# --------------------------------------------------------------------------
def _sort_score(h: dict):
    """More matched terms is better, but real-world adoption counts too:
    marketplace descriptions match many generic words ('commit', 'workflow'),
    while a skills.sh hit matches one term yet carries an install count. The
    boost keeps a popular, on-topic registry skill from being pushed out by
    incidental multi-word matches."""
    installs = h.get("installs") or 0
    boost = 2 if installs >= 1000 else (1 if installs >= 50 else 0)
    return (-(len(h.get("matched_terms", [])) + boost), -installs, _norm(h.get("name")))


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
    return sorted(best.values(), key=_sort_score)


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


def _trim_with_diversity(cands: list[dict], top: int) -> list[dict]:
    """Trim to `top`, but never let the cut erase a whole source: if skills.sh
    (cli-registry) returned hits and none survived the head, swap the tail for
    the registry's best two. Registry hits carry install counts the ranking
    rules need, so dropping them all loses real signal."""
    head = cands[:top]
    if any(c.get("catalog_type") == "cli-registry" for c in head):
        return head
    registry = [c for c in cands if c.get("catalog_type") == "cli-registry"][:2]
    if not registry:
        return head
    return head[: max(0, top - len(registry))] + registry


def _job_terms(job: str) -> list[str]:
    """A job is a comma-separated list of search PHRASES ('git safety, commit
    push'). Phrases stay whole — skills.sh ranks 'git safety' far better than
    'git' and 'safety' separately, and marketplace matching ANDs the words of
    a phrase anyway. No commas -> the whole job string is one phrase."""
    return [t.strip() for t in job.split(",") if t.strip()]


def search_batch(scan: dict, jobs: list[str], top: int) -> dict:
    """Run one search() per job concurrently; trim each job to `top` candidates.
    Jobs are independent fan-outs, so the npx/wp latency overlaps instead of
    stacking."""
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(jobs)))) as ex:
        results = list(ex.map(lambda j: search(scan, _job_terms(j)), jobs))
    out_jobs = []
    errors: list[str] = []
    probes = {p.get("name"): p for r in results for p in r.get("needs_live_probe", [])}
    for job, r in zip(jobs, results):
        cands = _trim_with_diversity(r.get("candidates", []), top)
        # Compact: long descriptions blow up the live session's context.
        for c in cands:
            if len(c.get("description") or "") > 220:
                c["description"] = c["description"][:217] + "..."
        out_jobs.append({"job": job, "candidates": cands})
        errors += r.get("errors", [])
    return {"jobs": out_jobs,
            "needs_live_probe": sorted(probes.values(), key=lambda p: str(p.get("name"))),
            "errors": sorted(set(errors))}


def main() -> int:
    ap = argparse.ArgumentParser(description="skills-daimon catalog search (verified candidates only)")
    ap.add_argument("--scan", required=True, help="scan.py JSON path")
    ap.add_argument("--terms", nargs="+", help="job query terms (single-job mode)")
    ap.add_argument("--jobs",
                    help='pipe-separated jobs; commas separate phrases within a job, '
                         'e.g. "git safety, commit push|sql query, data analysis"')
    ap.add_argument("--top", type=int, default=6, help="max candidates per job (batch mode)")
    args = ap.parse_args()
    if not args.terms and not args.jobs:
        ap.error("one of --terms or --jobs is required")
    scan = json.loads(Path(args.scan).read_text())
    if args.jobs:
        jobs = [j.strip() for j in args.jobs.split("|") if j.strip()]
        out = search_batch(scan, jobs, args.top)
    else:
        out = search(scan, args.terms)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
