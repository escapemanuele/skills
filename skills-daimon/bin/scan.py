#!/usr/bin/env python3
"""
skills-daimon session scanner.

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
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime as _dt
from pathlib import Path

# Shared secret-redactor (applied at the write boundary).
sys.path.insert(0, str(Path(__file__).parent))
from redact import redact, redact_in  # noqa: E402


PROMPT_MAX = 160
TOP_N = 25
SUMMARY_CAP_BYTES = 60_000

# --- Outcome / friction / completion (PR α) ---------------------------------
USAGE_DATA = Path.home() / ".claude" / "usage-data"
FACETS_DIR = USAGE_DATA / "facets"
META_DIR = USAGE_DATA / "session-meta"

# Anthropic enum values (raw — pretty labels live in the renderer only).
OUTCOME_ENUMS = (
    "fully_achieved", "mostly_achieved", "partially_achieved",
    "not_achieved", "unclear_from_transcript",
)
FRICTION_ENUMS = (
    "wrong_approach", "buggy_code", "misunderstood_request",
    "user_rejected_action", "external_blocker", "excessive_changes",
)

# PR URL detection (for opportunistic gh-pr-detected count)
PR_URL_RE = re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+/pull/\d+")
GH_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")

# Memory event detection on user content
MEMORY_REMEMBER_RE = re.compile(r"(?:^|\s)/remember\b", re.IGNORECASE)
MEMORY_PATH_HINTS = ("/.claude/memory", "/.claude/projects/", "MEMORY.md")


def load_facet(session_id: str) -> dict | None:
    """Read ~/.claude/usage-data/facets/<session_id>.json if present."""
    p = FACETS_DIR / f"{session_id}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def load_session_meta(session_id: str) -> dict | None:
    """Read ~/.claude/usage-data/session-meta/<session_id>.json if present."""
    p = META_DIR / f"{session_id}.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def session_id_from_path(path: Path) -> str:
    """Session UUID from the jsonl filename."""
    return path.stem


def tool_results_from_user(message) -> list[dict]:
    """Pull tool_result blocks out of a user-type event's message.content."""
    out = []
    msg = message.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                out.append(block)
    return out


def is_memory_path(p: str | None) -> bool:
    if not p:
        return False
    return any(h in p for h in MEMORY_PATH_HINTS) or p.endswith("/MEMORY.md")


# --- Stuck-loop helpers (PR β) ----------------------------------------------
STUCK_GAP_SECONDS = 120


def _parse_ts(s: str):
    try:
        return _dt.fromisoformat((s or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _sha8(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()[:8]


def _cmd_summary(cmd: str) -> str:
    """Short, redaction-friendly preview of a stuck command (3 words max)."""
    parts = cmd.strip().split()
    head = " ".join(parts[:3])[:40]
    return head + " …" if len(parts) > 3 else head

IGNORED_FILE = Path.home() / ".claude" / "skills" / "skills-daimon" / ".ignored.json"


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

    # skills.sh — the public open skills registry, reachable via the `skills`
    # CLI run through `npx`. We only require `npx` to be present; the package
    # is fetched on demand. This is the largest catalog by far, so add it
    # whenever Node/npx is available.
    if shutil.which("npx"):
        catalogs.append(
            {
                "name": "skills.sh",
                "type": "cli-registry",
                "tool": "npx skills find",
                "install_tool": "npx skills add",
                "init_tool": "npx skills init",
            }
        )

    return catalogs


def discover_mcp_servers(cwd: Path) -> list[dict]:
    """Find configured MCP servers that may expose a skill/plugin catalog.

    The scanner runs as a plain subprocess and CANNOT call MCP tools — only
    the live Claude session can. So we can't *probe* an MCP server here. What
    we can do is read the MCP config files, list the configured servers, and
    emit each as an `mcp-server` catalog candidate. SKILL.md then tells Claude
    (which does have MCP access) to enumerate each server's providers/tools and
    keep the ones that look like a catalog (a `search`+`get` pair with a
    catalog-flavored description, e.g. context-a8c's `ai-skills`).

    Sources, in order: global `~/.claude.json` top-level `mcpServers`, plus the
    current project's `mcpServers` block in the same file, plus a project-local
    `.mcp.json`. Server names are deduped.
    """
    seen: set[str] = set()
    servers: list[dict] = []

    def _add(names):
        for n in names:
            if n and n not in seen:
                seen.add(n)
                servers.append(
                    {
                        "name": n,
                        "type": "mcp-server",
                        "probe": (
                            "Claude-side only: load/enumerate this server's "
                            "providers and tools; treat any provider exposing a "
                            "search+get pair with a catalog-flavored description "
                            "(skill/plugin/agent/marketplace/directory/catalog) "
                            "as a catalog and query it in Step 3."
                        ),
                    }
                )

    home = Path.home()
    claude_json = home / ".claude.json"
    if claude_json.is_file():
        try:
            data = json.loads(claude_json.read_text())
        except Exception:
            data = {}
        # Global servers
        if isinstance(data.get("mcpServers"), dict):
            _add(data["mcpServers"].keys())
        # Current project's servers
        projects = data.get("projects") or {}
        proj = projects.get(str(cwd))
        if isinstance(proj, dict) and isinstance(proj.get("mcpServers"), dict):
            _add(proj["mcpServers"].keys())

    # Project-local .mcp.json
    mcp_json = cwd / ".mcp.json"
    if mcp_json.is_file():
        try:
            data = json.loads(mcp_json.read_text())
            if isinstance(data.get("mcpServers"), dict):
                _add(data["mcpServers"].keys())
        except Exception:
            pass

    return servers


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

# --- Coaching detectors (deterministic, no LLM) -----------------------------
# Bash verbs whose job a native Claude tool does better/structured. Heavy use
# of these usually means the session reached for the shell instead of the tool.
SEARCH_READ_BYPASS = {
    "grep": "Grep",
    "rg": "Grep",
    "find": "Glob",
    "cat": "Read",
    "head": "Read",
    "tail": "Read",
    "sed": "Read/Edit",
    "awk": "Read/Grep",
}

# Risky commands worth flagging if they appear without an obvious safety net.
# Each pattern -> short human label. Matched case-insensitively on the full cmd.
DESTRUCTIVE_PATTERNS = [
    (re.compile(r"\bgit\s+push\s+(?:[^|&;]*\s)?(?:--force(?!-with-lease)|-f)\b"), "git push --force"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard"),
    # git clean with -f and -d in either order (each branch anchored to `git clean`)
    (re.compile(r"\bgit\s+clean\s+-\w*f\w*d\w*\b|\bgit\s+clean\s+-\w*d\w*f\w*\b"), "git clean -fd"),
    (re.compile(r"--no-verify\b"), "--no-verify (skips hooks)"),
    # rm with -r and -f in either order (each branch anchored to `rm`)
    (re.compile(r"\brm\s+-\w*r\w*f\w*\b|\brm\s+-\w*f\w*r\w*\b"), "rm -rf"),
]

CURL_HOST_RE = re.compile(r"\b(?:curl|wget)\b[^|&;]*?https?://([^/\s'\"]+)", re.IGNORECASE)

# --- Work-recap signal buckets (classify what a project/session is about) ---
# Bash verbs (base token) that signal hands-on software development.
DEV_VERBS = {
    "git", "gh", "npm", "yarn", "pnpm", "composer", "php", "node", "npx",
    "make", "docker", "kubectl", "cargo", "go", "tsc", "eslint", "jest",
    "vitest", "pytest", "ruby", "rails", "bundle",
}
# MCP-name substrings that signal data work vs ops/personal work.
DATA_MCP_HINTS = ("trino", "sql", "bigquery", "snowflake", "duckdb")
OPS_MCP_HINTS = ("gmail", "calendar", "slack", "telegram", "discord", "notion")
# File extensions that mean prose/docs (Write/Edit on these = writing, not dev).
PROSE_EXT = {"md", "markdown", "mdx", "txt", "rst", "org", "tex", "adoc"}


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

    # --- coaching signals ---
    total_bash = 0
    bypass_calls = collections.Counter()       # native-tool bypass verbs
    destructive = collections.Counter()        # label -> count
    destructive_samples: dict[str, str] = {}   # label -> one real command
    curl_hosts = collections.Counter()         # host -> count
    project_cwd: dict[str, str] = {}           # project dir name -> real cwd

    # --- per-project tokens (for the work recap; not cost) ---
    proj_tokens = collections.Counter()        # project -> total tokens (in+out)
    proj_branch: dict[str, str] = {}           # project -> gitBranch

    # --- work-recap signals ---
    work_mix = collections.Counter()           # global: dev/data/writing/ops
    proj_signal: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    # --- PR α: outcomes (from facets), completion (from session-meta),
    #         tool errors (via tool_use_id→name), memory events, gh-PR ----
    outcomes_by_facet = collections.Counter()
    friction_sessions = collections.Counter()       # session-incidence per friction
    friction_counts_sum = collections.Counter()     # summed raw counts per friction
    primary_success_top = collections.Counter()
    session_type_mix = collections.Counter()
    helpfulness_mix = collections.Counter()
    outcomes_labeled = 0                            # sessions with a readable facet

    completion_commits = 0
    completion_pushes = 0
    completion_lines_added = 0
    completion_lines_removed = 0
    completion_files_modified = 0
    completion_with_meta = 0
    proj_commits = collections.Counter()
    proj_pushes = collections.Counter()

    tool_errors_ok = collections.Counter()
    tool_errors_err = collections.Counter()

    memory_remember = 0
    memory_edits = 0
    memory_sessions = 0

    prs_via_gh_create = 0
    prs_via_url = 0

    # --- PR β: stuck-loop detection (per session). Raw command is kept ONLY
    #          for this current run's emit; nothing here lands in history.jsonl.
    stuck_loops: list[dict] = []

    for project, path, mtime in iter_session_files(root, max_age_days):
        session_user_msgs = 0
        session_bash = 0
        sid = session_id_from_path(path)

        # Outcomes (facets/<sid>.json) — may be missing (lag is normal); silent skip.
        facet = load_facet(sid)
        if isinstance(facet, dict):
            outcomes_labeled += 1
            oc = facet.get("outcome")
            if isinstance(oc, str):
                outcomes_by_facet[oc] += 1
            ps = facet.get("primary_success")
            if isinstance(ps, str) and ps:
                primary_success_top[ps] += 1
            st = facet.get("session_type")
            if isinstance(st, str) and st:
                session_type_mix[st] += 1
            ch = facet.get("claude_helpfulness")
            if isinstance(ch, str) and ch:
                helpfulness_mix[ch] += 1
            fc = facet.get("friction_counts") or {}
            if isinstance(fc, dict):
                for k, v in fc.items():
                    if not isinstance(v, (int, float)) or v <= 0:
                        continue
                    friction_sessions[k] += 1          # incidence: session had it
                    friction_counts_sum[k] += int(v)   # intensity: total events

        # Completion (session-meta/<sid>.json) — may also be missing.
        meta = load_session_meta(sid)
        if isinstance(meta, dict):
            completion_with_meta += 1
            gc = int(meta.get("git_commits") or 0)
            gp = int(meta.get("git_pushes") or 0)
            if gc > 0:
                completion_commits += 1
                proj_commits[project] += gc
            if gp > 0:
                completion_pushes += 1
                proj_pushes[project] += gp
            completion_lines_added += int(meta.get("lines_added") or 0)
            completion_lines_removed += int(meta.get("lines_removed") or 0)
            completion_files_modified += int(meta.get("files_modified") or 0)

        # Per-session maps reset.
        tool_use_id_to_name: dict[str, str] = {}
        session_has_memory = False
        # (timestamp ISO, command_string) per Bash use — for stuck-loop detection.
        session_bash_events: list[tuple[str, str]] = []

        try:
            with path.open() as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    # Capture the real working directory once per project (events
                    # carry an absolute `cwd`); used later for CLAUDE.md checks.
                    if project not in project_cwd:
                        cwd_val = ev.get("cwd")
                        if isinstance(cwd_val, str) and cwd_val:
                            project_cwd[project] = cwd_val
                    if project not in proj_branch:
                        br = ev.get("gitBranch")
                        if isinstance(br, str) and br and br != "HEAD":
                            proj_branch[project] = br
                    typ = ev.get("type")
                    if typ == "user":
                        msg = ev.get("message") or {}
                        # tool_result blocks → bucket errors by mapped tool name
                        # + scan their text for PR URLs (opportunistic, labeled).
                        for tr in tool_results_from_user(ev):
                            tu_id = tr.get("tool_use_id") or ""
                            tname = tool_use_id_to_name.get(tu_id, "?")
                            if tr.get("is_error") is True:
                                tool_errors_err[tname] += 1
                            else:
                                tool_errors_ok[tname] += 1
                            rc = tr.get("content")
                            if isinstance(rc, str):
                                rt = rc
                            elif isinstance(rc, list):
                                rt = " ".join(
                                    b.get("text", "") for b in rc
                                    if isinstance(b, dict) and b.get("type") == "text"
                                )
                            else:
                                rt = ""
                            if rt and PR_URL_RE.search(rt):
                                prs_via_url += 1
                        text = content_to_text(msg.get("content"))
                        if not is_caveat_or_system(text):
                            session_user_msgs += 1
                            normalized = normalize_prompt(text)
                            if normalized:
                                prompt_counter[normalized] += 1
                            # /remember invocation in user content
                            if text and MEMORY_REMEMBER_RE.search(text):
                                memory_remember += 1
                                session_has_memory = True
                    elif typ == "assistant":
                        # Per-project token volume (input+output) for the work recap.
                        msg = ev.get("message") or {}
                        usage = msg.get("usage") or {}
                        if usage:
                            proj_tokens[project] += (
                                int(usage.get("input_tokens") or 0)
                                + int(usage.get("output_tokens") or 0)
                            )
                        for tu in tool_uses_from_assistant(ev):
                            name = tu.get("name", "?")
                            tool_uses[name] += 1
                            # Record id→name so user-side tool_result blocks
                            # can be bucketed by tool name later.
                            tu_id = tu.get("id")
                            if tu_id:
                                tool_use_id_to_name[tu_id] = name
                            # Work-recap classification (one bucket per tool use).
                            # Write/Edit count as dev unless the file is prose/docs.
                            if name in ("Write", "Edit", "NotebookEdit"):
                                inp = tu.get("input") or {}
                                fp = inp.get("file_path") or inp.get("notebook_path") or ""
                                # Memory event: explicit edit to a memory path
                                if is_memory_path(fp):
                                    memory_edits += 1
                                    session_has_memory = True
                                ext = fp.rsplit(".", 1)[-1].lower() if "." in fp else ""
                                bucket = "writing" if ext in PROSE_EXT else "dev"
                                work_mix[bucket] += 1
                                proj_signal[project][bucket] += 1
                            elif name.startswith("mcp__"):
                                low = name.lower()
                                if any(h in low for h in DATA_MCP_HINTS):
                                    work_mix["data"] += 1
                                    proj_signal[project]["data"] += 1
                                elif any(h in low for h in OPS_MCP_HINTS):
                                    work_mix["ops"] += 1
                                    proj_signal[project]["ops"] += 1
                            if name == "Bash":
                                session_bash += 1
                                total_bash += 1
                                cmd = (tu.get("input") or {}).get("command", "")
                                # Stuck-loop input: keep (timestamp, cmd) — raw
                                # cmd stays only in this scan; never in history.
                                ts = ev.get("timestamp") or ""
                                if cmd:
                                    session_bash_events.append((ts, cmd))
                                verb = extract_bash_verb(cmd)
                                if verb:
                                    bash_verbs[verb] += 1
                                    if len(bash_verb_samples[verb]) < SAMPLES_PER_VERB:
                                        normalized = re.sub(r"\s+", " ", cmd.strip())[:SAMPLE_CMD_MAX_LEN]
                                        # Dedupe: skip if we already captured this exact command for this verb
                                        if normalized and normalized not in bash_verb_sample_seen[verb]:
                                            bash_verb_samples[verb].append(normalized)
                                            bash_verb_sample_seen[verb].add(normalized)
                                    # Native-tool bypass: count the base verb only
                                    base = verb.split()[0]
                                    if base in SEARCH_READ_BYPASS:
                                        bypass_calls[base] += 1
                                    # Work-recap: dev signal from build/VCS verbs
                                    if base in DEV_VERBS:
                                        work_mix["dev"] += 1
                                        proj_signal[project]["dev"] += 1
                                # Destructive command patterns (match full cmd)
                                for pat, label in DESTRUCTIVE_PATTERNS:
                                    if pat.search(cmd):
                                        destructive[label] += 1
                                        destructive_samples.setdefault(
                                            label,
                                            re.sub(r"\s+", " ", cmd.strip())[:SAMPLE_CMD_MAX_LEN],
                                        )
                                # Raw HTTP against a host (curl/wget)
                                m = CURL_HOST_RE.search(cmd)
                                if m:
                                    curl_hosts[m.group(1)] += 1
                                # gh pr create — opportunistic PR detection
                                if GH_PR_CREATE_RE.search(cmd):
                                    prs_via_gh_create += 1
                            elif name == "WebFetch":
                                web_fetches += 1
                            elif name.startswith("mcp__"):
                                # Group mcp calls by server__tool
                                mcp_calls[name] += 1
        except OSError:
            continue

        project_counts[project] += 1
        if session_has_memory:
            memory_sessions += 1

        # --- Stuck-loop runs in this session: ≥3 identical Bash commands in a
        # row with ≤2 min between consecutive calls. Honest polling is dropped
        # by the gap rule (deploys, CI watches usually wait longer).
        if len(session_bash_events) >= 3:
            run_start = 0
            while run_start < len(session_bash_events):
                cmd = session_bash_events[run_start][1]
                run_end = run_start + 1
                while run_end < len(session_bash_events):
                    if session_bash_events[run_end][1] != cmd:
                        break
                    tp = _parse_ts(session_bash_events[run_end - 1][0])
                    tc = _parse_ts(session_bash_events[run_end][0])
                    gap = (tc - tp).total_seconds() if (tp and tc) else 0
                    if gap > STUCK_GAP_SECONDS:
                        break
                    run_end += 1
                length = run_end - run_start
                if length >= 3:
                    stuck_loops.append({
                        "command_hash": _sha8(cmd),
                        "command_summary": _cmd_summary(cmd),
                        "command": cmd[:SAMPLE_CMD_MAX_LEN],  # current run only; redactor masks
                        "count": length,
                        "session": sid,
                        "first_ts": session_bash_events[run_start][0],
                        "last_ts": session_bash_events[run_end - 1][0],
                    })
                run_start = run_end if run_end > run_start else run_start + 1

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

    # --- Build coaching signals (deterministic; Claude turns these into advice) ---
    # Hot repos missing a CLAUDE.md: git repos with >=3 sessions whose real cwd
    # exists on disk but has no CLAUDE.md (so context gets re-explained each run).
    # Require a .git dir so we only flag actual repos, not parent/home dirs.
    hot_repos_no_claudemd = []
    for proj, n in project_counts.most_common():
        if n < 3:
            continue
        real = project_cwd.get(proj)
        if not real:
            continue
        p = Path(real)
        try:
            if not (p.is_dir() and (p / ".git").exists()):
                continue
            if not (p / "CLAUDE.md").exists():
                hot_repos_no_claudemd.append({"path": real, "sessions": n})
        except OSError:
            continue
    hot_repos_no_claudemd = hot_repos_no_claudemd[:8]

    native_native_use = {
        t: tool_uses.get(t, 0) for t in ("Grep", "Glob", "Read")
    }
    coaching_signals = {
        # Native-tool bypass: shell verbs that duplicate a Claude tool.
        "native_tool_bypass": {
            "bash_total": total_bash,
            "bypass_calls": dict(bypass_calls.most_common()),
            "bypass_total": sum(bypass_calls.values()),
            "suggested_tool": {v: SEARCH_READ_BYPASS[v] for v in bypass_calls},
            "native_tool_use": native_native_use,
        },
        # Risky commands seen (with one real sample each).
        "destructive_cmds": [
            {"label": lbl, "count": destructive[lbl], "sample": destructive_samples.get(lbl, "")}
            for lbl, _ in destructive.most_common()
        ],
        # Raw HTTP to hosts that may have a dedicated CLI/MCP.
        "raw_http_hosts": dict(curl_hosts.most_common(10)),
        # Foreground sleeps usually mean polling instead of a proper wait.
        "sleep_calls": bash_verbs.get("sleep", 0),
        # Hot repos with no CLAUDE.md (context re-explained each session).
        "hot_repos_without_claudemd": hot_repos_no_claudemd,
    }

    # --- Work recap: top projects (kind-tagged) + overall mix ---
    top_projects = []
    for proj, n in project_counts.most_common(6):
        sig = proj_signal.get(proj)
        kind = sig.most_common(1)[0][0] if sig else "other"
        top_projects.append({
            "path": project_cwd.get(proj, proj),
            "sessions": n,
            "tokens": proj_tokens.get(proj, 0),
            "kind": kind,
            "branch": proj_branch.get(proj),
            "commits": int(proj_commits.get(proj, 0)),
            "pushes": int(proj_pushes.get(proj, 0)),
        })
    mix_total = sum(work_mix.values())
    work_recap = {
        "top_projects": top_projects,
        "mix": {
            k: round(100 * v / mix_total)
            for k, v in work_mix.most_common()
        } if mix_total else {},
    }

    # --- PR α: outcomes / completion / tool_errors / memory_events --------
    total_sessions = len(sessions)
    outcomes = {
        "by_facet": dict(outcomes_by_facet),
        "friction_sessions": dict(friction_sessions.most_common()),
        "friction_counts_sum": dict(friction_counts_sum.most_common()),
        "primary_success_top": dict(primary_success_top.most_common()),
        "session_type_mix": dict(session_type_mix.most_common()),
        "helpfulness_mix": dict(helpfulness_mix.most_common()),
        "coverage": {"labeled": outcomes_labeled, "total": total_sessions},
    }
    completion = {
        "sessions_with_commit": completion_commits,
        "sessions_with_push": completion_pushes,
        "lines_added": completion_lines_added,
        "lines_removed": completion_lines_removed,
        "files_modified": completion_files_modified,
        "prs_detected_via_gh": {
            "gh_pr_create": prs_via_gh_create,
            "pr_url_in_results": prs_via_url,
        },
        "coverage": {"with_meta": completion_with_meta, "total": total_sessions},
    }
    tool_errors = {
        name: {"ok": tool_errors_ok.get(name, 0), "error": tool_errors_err.get(name, 0)}
        for name in sorted(set(tool_errors_ok) | set(tool_errors_err))
    }
    memory_events = {
        "remember_invocations": memory_remember,
        "memory_file_edits": memory_edits,
        "sessions_with_memory": memory_sessions,
    }

    # Catalog probing is expensive (cold path can run ~1 min on machines with
    # many CLI providers). Cache to ~/.claude/skills/skills-daimon/.catalogs-cache.json
    # with a 24h TTL.
    cache = Path.home() / ".claude" / "skills" / "skills-daimon" / ".catalogs-cache.json"
    catalogs = load_cached_catalogs(cache, 86400)
    if catalogs is None:
        catalogs = discover_catalogs()
        save_cached_catalogs(cache, catalogs)

    # MCP-server catalog candidates are discovered from config files (cheap, no
    # probing) and depend on cwd, so compute them fresh each run rather than
    # caching. The live Claude session probes them — the scanner cannot.
    catalogs = catalogs + discover_mcp_servers(cwd or Path.cwd())

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
        "coaching_signals": coaching_signals,
        "work_recap": work_recap,
        "outcomes": outcomes,
        "completion": completion,
        "tool_errors": tool_errors,
        "memory_events": memory_events,
        "stuck_loops": stuck_loops,
    }

    # Redact at the write boundary: walk the whole summary and mask plausible
    # secrets in every string leaf. Catches anything in bash_verb_samples,
    # destructive_samples, raw_http_hosts, recurring prompts, etc.
    summary = redact_in(summary)

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
    parser.add_argument("--days", type=int, default=28, help="Days to look back (default 28).")
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
