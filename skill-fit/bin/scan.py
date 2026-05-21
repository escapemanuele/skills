#!/usr/bin/env python3
"""
skill-fit session scanner.

Reads ~/.claude/projects/*/*.jsonl session files, extracts deterministic
signals (no LLM), and emits a compact JSON summary suitable for Claude to
reason over.

The goal is hard counts, not vibes: if Claude later claims "you ran gh pr
view 14 times," the 14 comes from here.

Privacy: reads only local files. Prompts are truncated. No upload.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time
from pathlib import Path


PROMPT_MAX = 160
TOP_N = 25
SUMMARY_CAP_BYTES = 60_000

IGNORED_FILE = Path.home() / ".claude" / "skills" / "skill-fit" / ".ignored.json"


def load_ignored() -> list[str]:
    """Load list of skill/plugin names the user has dismissed."""
    try:
        data = json.loads(IGNORED_FILE.read_text())
        if isinstance(data, list):
            return sorted({str(x) for x in data if x})
    except (OSError, ValueError):
        pass
    return []


def add_ignored(names: list[str]) -> list[str]:
    """Append names to the ignored list, dedupe, persist. Return full list."""
    current = set(load_ignored())
    current.update(n.strip() for n in names if n.strip())
    out = sorted(current)
    IGNORED_FILE.parent.mkdir(parents=True, exist_ok=True)
    IGNORED_FILE.write_text(json.dumps(out, indent=2))
    return out


def remove_ignored(names: list[str]) -> list[str]:
    """Remove names from the ignored list, persist. Return full list."""
    current = set(load_ignored())
    for n in names:
        current.discard(n.strip())
    out = sorted(current)
    if out:
        IGNORED_FILE.write_text(json.dumps(out, indent=2))
    elif IGNORED_FILE.exists():
        IGNORED_FILE.unlink()
    return out

SKILL_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


def read_skill_name(skill_md: Path) -> str | None:
    try:
        head = skill_md.read_text(errors="ignore")[:2000]
    except OSError:
        return None
    m = SKILL_NAME_RE.search(head)
    return m.group(1) if m else None


def discover_catalogs() -> list[dict]:
    """Find catalog sources reachable from this machine.

    Returns a list of catalog descriptors. Each entry is:
      {"name": "<marketplace name>", "type": "marketplace",
       "marketplace_json": "<absolute path>", "plugin_count": <int>}
      or
      {"name": "<provider>", "type": "cli-provider", "tool": "wp context <provider>"}

    Local marketplaces are always discovered from the filesystem. CLI-provider
    catalogs are discovered by enumerating `wp context --list-providers` and
    probing each provider for `search`+`get` tools whose schema describes a
    skill/plugin/agent catalog.
    """
    import shutil
    import subprocess

    catalogs: list[dict] = []
    home = Path.home()

    # Local marketplaces (portable)
    mp_dir = home / ".claude" / "plugins" / "marketplaces"
    if mp_dir.is_dir():
        for sub in sorted(mp_dir.iterdir()):
            mj = sub / ".claude-plugin" / "marketplace.json"
            if not mj.is_file():
                continue
            try:
                data = json.loads(mj.read_text())
            except Exception:
                continue
            catalogs.append(
                {
                    "name": data.get("name") or sub.name,
                    "type": "marketplace",
                    "marketplace_json": str(mj),
                    "plugin_count": len(data.get("plugins") or []),
                }
            )

    # CLI-provider catalogs (any provider exposed via `wp context` that has
    # both `search` and `get` tools and a catalog-flavored description is
    # treated as a skill/plugin catalog). No provider name is hardcoded —
    # we ask `wp context --list-providers` for the list and probe each in
    # parallel.
    if shutil.which("wp"):
        try:
            r = subprocess.run(
                ["wp", "context", "--list-providers"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            providers = json.loads(r.stdout) if r.returncode == 0 else []
        except Exception:
            providers = []

        if providers:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # The search tool description should describe a catalog of
            # skills/plugins/agents — otherwise the provider has incidental
            # search/get tools (slack message search, linear issue search, etc.)
            # and isn't a catalog source.
            catalog_keywords = (
                "skill", "plugin", "agent", "marketplace",
                "directory", "catalog",
            )

            def _probe(p):
                try:
                    r = subprocess.run(
                        ["wp", "context", p, "--list-tools"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if r.returncode != 0:
                        return None
                    data = json.loads(r.stdout)
                    tools_by_name = {
                        t.get("name"): t for t in (data.get("tools") or [])
                    }
                    if "search" not in tools_by_name or "get" not in tools_by_name:
                        return None
                    desc = (tools_by_name["search"].get("description") or "").lower()
                    if any(k in desc for k in catalog_keywords):
                        return p
                except Exception:
                    return None
                return None

            with ThreadPoolExecutor(max_workers=4) as ex:
                for fut in as_completed([ex.submit(_probe, p) for p in providers]):
                    name = fut.result()
                    if name:
                        catalogs.append(
                            {
                                "name": name,
                                "type": "cli-provider",
                                "tool": f"wp context {name}",
                            }
                        )

    return catalogs


def load_cached_catalogs(cache_path: Path, max_age_seconds: int) -> list[dict] | None:
    """Return cached catalog list if fresh, else None."""
    try:
        st = cache_path.stat()
    except OSError:
        return None
    if time.time() - st.st_mtime > max_age_seconds:
        return None
    try:
        return json.loads(cache_path.read_text())
    except Exception:
        return None


def save_cached_catalogs(cache_path: Path, catalogs: list[dict]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(catalogs))
    except OSError:
        pass


def discover_installed(cwd: Path) -> dict:
    """Find skills and plugins already installed on this machine.

    Returns {"skills": [...], "plugins": [...]} — sorted, deduped.
    """
    home = Path.home()
    skill_md_globs = [
        # Standalone user-installed skills
        home / ".claude" / "skills",
        # Project-local skills (only the current working directory)
        cwd / ".claude" / "skills",
    ]
    skills: set[str] = set()
    for base in skill_md_globs:
        if not base.is_dir():
            continue
        for md in base.glob("*/SKILL.md"):
            name = read_skill_name(md)
            if name:
                skills.add(name)

    # Plugin-bundled skills: ~/.claude/plugins/cache/<mp>/<plugin>/<ver>/skills/*/SKILL.md
    plugin_cache = home / ".claude" / "plugins" / "cache"
    if plugin_cache.is_dir():
        for md in plugin_cache.glob("*/*/*/skills/*/SKILL.md"):
            name = read_skill_name(md)
            if name:
                skills.add(name)

    # Installed plugins themselves
    plugins: set[str] = set()
    installed_json = home / ".claude" / "plugins" / "installed_plugins.json"
    if installed_json.is_file():
        try:
            data = json.loads(installed_json.read_text())
            for key in (data.get("plugins") or {}).keys():
                # Keys look like "<plugin-name>@<marketplace>"
                plugins.add(key.split("@", 1)[0])
        except Exception:
            pass

    return {
        "skills": sorted(skills),
        "plugins": sorted(plugins),
    }


def iter_session_files(root: Path, max_age_days: int):
    cutoff = time.time() - max_age_days * 86400
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                yield project_dir.name, f, mtime


def extract_bash_verb(cmd: str) -> str | None:
    """Pull a normalized first verb out of a bash command line."""
    cmd = cmd.strip()
    if not cmd:
        return None
    # Drop leading env assignments like FOO=bar
    parts = re.split(r"\s+", cmd, maxsplit=4)
    while parts and "=" in parts[0] and not parts[0].startswith("--"):
        parts = parts[1:]
    if not parts:
        return None
    head = parts[0].lstrip("(").lstrip("!")
    # For things like `gh pr view`, return the two-token verb to give richer signal
    if head in {"gh", "git", "npm", "pnpm", "yarn", "wp", "composer", "php", "kubectl", "docker"} and len(parts) > 1:
        sub = parts[1]
        if sub.isalpha() or "-" in sub:
            return f"{head} {sub}"
    return head


def normalize_prompt(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:PROMPT_MAX]


def is_caveat_or_system(text: str) -> bool:
    """Skip command-caveat noise and other harness-generated prompts."""
    if not text:
        return True
    head = text.lstrip()[:300]
    NOISE_PREFIXES = (
        "<local-command-caveat>",
        "<local-command-stdout>",
        "<command-name>",
        "<command-message>",
        "<command-args>",
        "<system-reminder>",
        "<bash-stdout>",
        "<bash-stderr>",
        "<task-notification>",
        "[Request interrupted",
        "Base directory for this skill:",
        "Caveat: The messages",
    )
    if head.startswith(NOISE_PREFIXES):
        return True
    # Single-word "continue" / "resume" / "next" style prompts are noise too.
    stripped = text.strip().lower()
    if stripped in {"resume", "continue", "next", "ok", "yes", "no", "go"}:
        return True
    return False


def content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(block.get("text", ""))
        return "\n".join(out)
    return ""


def tool_uses_from_assistant(message) -> list[dict]:
    out = []
    msg = message.get("message") or {}
    for block in msg.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            out.append(block)
    return out


SAMPLE_CMD_MAX_LEN = 200
SAMPLES_PER_VERB = 5


def scan(root: Path, max_age_days: int, cwd: Path | None = None) -> dict:
    sessions = []
    bash_verbs = collections.Counter()
    bash_verb_samples: dict[str, list[str]] = collections.defaultdict(list)
    bash_verb_sample_seen: dict[str, set[str]] = collections.defaultdict(set)
    tool_uses = collections.Counter()
    mcp_calls = collections.Counter()
    web_fetches = 0
    prompts = []  # list of (count, text)
    prompt_counter = collections.Counter()

    project_counts = collections.Counter()

    for project, path, mtime in iter_session_files(root, max_age_days):
        session_user_msgs = 0
        session_bash = 0
        try:
            with path.open() as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    typ = ev.get("type")
                    if typ == "user":
                        msg = ev.get("message") or {}
                        text = content_to_text(msg.get("content"))
                        if not is_caveat_or_system(text):
                            session_user_msgs += 1
                            normalized = normalize_prompt(text)
                            if normalized:
                                prompt_counter[normalized] += 1
                    elif typ == "assistant":
                        for tu in tool_uses_from_assistant(ev):
                            name = tu.get("name", "?")
                            tool_uses[name] += 1
                            if name == "Bash":
                                session_bash += 1
                                cmd = (tu.get("input") or {}).get("command", "")
                                verb = extract_bash_verb(cmd)
                                if verb:
                                    bash_verbs[verb] += 1
                                    if len(bash_verb_samples[verb]) < SAMPLES_PER_VERB:
                                        normalized = re.sub(r"\s+", " ", cmd.strip())[:SAMPLE_CMD_MAX_LEN]
                                        # Dedupe: skip if we already captured this exact command for this verb
                                        if normalized and normalized not in bash_verb_sample_seen[verb]:
                                            bash_verb_samples[verb].append(normalized)
                                            bash_verb_sample_seen[verb].add(normalized)
                            elif name == "WebFetch":
                                web_fetches += 1
                            elif name.startswith("mcp__"):
                                # Group mcp calls by server__tool
                                mcp_calls[name] += 1
        except OSError:
            continue

        project_counts[project] += 1
        sessions.append(
            {
                "project": project,
                "file": path.name,
                "mtime": int(mtime),
                "user_msgs": session_user_msgs,
                "bash_calls": session_bash,
            }
        )

    # Pick top repeated prompts (recurring >= 2)
    recurring = [
        {"prompt": p, "count": n}
        for p, n in prompt_counter.most_common(TOP_N)
        if n >= 2
    ]
    # And a smaller sample of one-off prompts (helps Claude see breadth)
    sampled_oneoffs = [
        {"prompt": p}
        for p, n in list(prompt_counter.items())
        if n == 1
    ][:30]

    installed = discover_installed(cwd or Path.cwd())

    # Catalog probing is expensive (cold path can run ~1 min on machines with
    # many CLI providers). Cache to ~/.claude/skills/skill-fit/.catalogs-cache.json
    # with a 24h TTL.
    cache = Path.home() / ".claude" / "skills" / "skill-fit" / ".catalogs-cache.json"
    catalogs = load_cached_catalogs(cache, 86400)
    if catalogs is None:
        catalogs = discover_catalogs()
        save_cached_catalogs(cache, catalogs)

    summary = {
        "scanned_root": str(root),
        "max_age_days": max_age_days,
        "session_count": len(sessions),
        "projects": dict(project_counts.most_common()),
        "tool_use_top": dict(tool_uses.most_common(TOP_N)),
        "mcp_calls_top": dict(mcp_calls.most_common(TOP_N)),
        "bash_verbs_top": dict(bash_verbs.most_common(TOP_N)),
        "bash_verb_samples": {
            v: bash_verb_samples[v]
            for v, _ in bash_verbs.most_common(TOP_N)
            if bash_verb_samples[v]
        },
        "web_fetches": web_fetches,
        "recurring_prompts": recurring,
        "sampled_oneoff_prompts": sampled_oneoffs,
        "session_index": sessions[-20:],
        "installed_skills": installed["skills"],
        "installed_plugins": installed["plugins"],
        "available_catalogs": catalogs,
        "ignored_names": load_ignored(),
    }

    serialized = json.dumps(summary)
    if len(serialized) > SUMMARY_CAP_BYTES:
        # Trim the least-essential lists until we fit.
        for key in ("sampled_oneoff_prompts", "session_index", "recurring_prompts"):
            while summary[key] and len(json.dumps(summary)) > SUMMARY_CAP_BYTES:
                summary[key].pop()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan recent Claude Code sessions.")
    parser.add_argument(
        "--root",
        default=str(Path.home() / ".claude" / "projects"),
        help="Path to ~/.claude/projects (default).",
    )
    parser.add_argument("--days", type=int, default=14, help="Days to look back (default 14).")
    parser.add_argument(
        "--cwd",
        default=None,
        help="Working directory to scan for project-local skills (default: $PWD).",
    )
    parser.add_argument(
        "--ignore",
        nargs="+",
        metavar="NAME",
        help="Add one or more skill/plugin names to the ignored list and exit (no scan).",
    )
    parser.add_argument(
        "--unignore",
        nargs="+",
        metavar="NAME",
        help="Remove one or more names from the ignored list and exit (no scan).",
    )
    parser.add_argument(
        "--list-ignored",
        action="store_true",
        help="Print the current ignored list and exit (no scan).",
    )
    args = parser.parse_args()

    if args.ignore:
        out = add_ignored(args.ignore)
        json.dump({"ignored_names": out}, sys.stdout)
        sys.stdout.write("\n")
        return 0
    if args.unignore:
        out = remove_ignored(args.unignore)
        json.dump({"ignored_names": out}, sys.stdout)
        sys.stdout.write("\n")
        return 0
    if args.list_ignored:
        json.dump({"ignored_names": load_ignored()}, sys.stdout)
        sys.stdout.write("\n")
        return 0

    root = Path(args.root)
    if not root.is_dir():
        print(json.dumps({"error": f"Sessions dir not found: {root}"}), file=sys.stdout)
        return 1
    cwd = Path(args.cwd) if args.cwd else None
    summary = scan(root, args.days, cwd)
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
