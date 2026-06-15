#!/usr/bin/env python3
"""
skills-daimon session scanner — OpenAI Codex source.

Reads ~/.codex/sessions/**/rollout-*.jsonl (and archived_sessions/) and emits
the SAME scan JSON schema that analyze.py consumes, so the rest of the pipeline
(analyze / catalog_search / render / finalize) is unchanged.

Codex rollout lines are {timestamp, type, payload}. The signal map:
  - session_meta / turn_context .cwd     -> projects, work_recap
  - response_item function_call           -> Bash/exec_command (cmd parsing)
    name == "exec_command", arguments.cmd
  - response_item custom_tool_call         -> Edit (apply_patch)
  - event_msg user_message.message         -> recurring_prompts
  - event_msg token_count                  -> per-session tokens
  - event_msg mcp_tool_call_end            -> mcp_calls
  - response_item tool_search_call         -> ToolSearch
  - function_call_output "exited with N"   -> Bash error rate

Honest degradations (Codex has no Anthropic outcome facets and no built-in
Grep/Glob/Read): `outcomes` ships empty (coverage.labeled == 0), and
native_tool_bypass ships bash_total/bypass_total == 0 so the "shell vs built-in
search" scorecard row and token tip fall back to no_data instead of misreporting.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

# Reuse the Claude scanner's host-neutral helpers so behaviour stays identical.
import scan as _scan

CODEX_HOME = Path.home() / ".codex"
ROLLOUT_GLOBS = ("sessions/**/rollout-*.jsonl", "archived_sessions/rollout-*.jsonl")


def _iter_rollouts(root: Path, max_age_days: int):
    """Yield rollout files whose mtime is within the window."""
    import time
    cutoff = time.time() - max_age_days * 86400
    seen = set()
    for pat in ROLLOUT_GLOBS:
        for p in root.glob(pat):
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            try:
                if p.stat().st_mtime >= cutoff:
                    yield p
            except OSError:
                continue


# Codex injects session-history/preamble blocks as "user" turns; these are not
# things the user typed, so they must not count as recurring prompts.
_CODEX_NOISE = re.compile(
    r"^(the following is the codex agent history|"
    r"<permissions instructions>|<environment_context>|"
    r"## my request for codex)", re.IGNORECASE)


def _is_codex_noise(text: str) -> bool:
    return bool(_CODEX_NOISE.match(text.strip()))


def _exec_cmd(payload: dict) -> str | None:
    """Pull the shell command string out of an exec_command function_call."""
    if payload.get("name") != "exec_command":
        return None
    args = payload.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if isinstance(args, dict):
        cmd = args.get("cmd")
        if isinstance(cmd, list):
            return " ".join(str(x) for x in cmd)
        if isinstance(cmd, str):
            return cmd
    return None


def _scan_one(path: Path) -> dict:
    """Parse a single rollout into per-session aggregates."""
    cwd = None
    cmds: list[str] = []
    prompts: list[str] = []
    tokens = 0
    exec_ok = exec_err = 0
    tool_use = collections.Counter()
    mcp_calls = collections.Counter()
    files_modified: set[str] = set()
    committed = pushed = False
    out_by_call: dict[str, str] = {}

    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rtype = rec.get("type")
        p = rec.get("payload") or {}
        if rtype in ("session_meta", "turn_context") and not cwd:
            cwd = p.get("cwd")
        elif rtype == "response_item":
            pt = p.get("type")
            if pt == "function_call":
                cmd = _exec_cmd(p)
                if cmd is not None:
                    cmds.append(cmd)
                    tool_use["Bash"] += 1
                else:
                    tool_use[p.get("name") or "function_call"] += 1
            elif pt == "custom_tool_call":
                name = p.get("name") or ""
                tool_use["Edit" if name == "apply_patch" else (name or "custom_tool_call")] += 1
            elif pt == "function_call_output":
                cid = p.get("call_id")
                if cid:
                    out_by_call[cid] = p.get("output") or ""
            elif pt in ("tool_search_call",):
                tool_use["ToolSearch"] += 1
            # NB: response_item message role=="user" is model-context (often
            # injected/compacted history), not a genuine user turn — skip it.
            # Real prompts come from event_msg user_message below.
        elif rtype == "event_msg":
            pt = p.get("type")
            if pt == "user_message":
                msg = p.get("message")
                if (isinstance(msg, str) and msg and not _scan.is_caveat_or_system(msg)
                        and not _is_codex_noise(msg)):
                    prompts.append(msg)
            elif pt == "token_count":
                info = p.get("info") or {}
                tot = (info.get("total_token_usage") or {}).get("total_tokens")
                if isinstance(tot, int):
                    tokens = max(tokens, tot)   # cumulative — keep the largest
            elif pt == "mcp_tool_call_end":
                name = p.get("tool") or p.get("name") or "mcp"
                mcp_calls[str(name)] += 1
                tool_use[str(name)] += 1
            elif pt == "patch_apply_end":
                for f in (p.get("changes") or {}):
                    files_modified.add(f)

    # exec_command exit codes from the matching outputs.
    for out in out_by_call.values():
        m = re.search(r"exited with code (\d+)", out)
        if m:
            if m.group(1) == "0":
                exec_ok += 1
            else:
                exec_err += 1
    for c in cmds:
        if re.search(r"\bgit\s+commit\b", c):
            committed = True
        if re.search(r"\bgit\s+push\b", c):
            pushed = True

    return {
        "cwd": cwd, "cmds": cmds, "prompts": prompts, "tokens": tokens,
        "exec_ok": exec_ok, "exec_err": exec_err, "tool_use": tool_use,
        "mcp_calls": mcp_calls, "files_modified": files_modified,
        "committed": committed, "pushed": pushed,
    }


def _classify_kind(cmds, prompts) -> str:
    """Coarse project kind from command verbs (mirror of scan's dev bucket).
    extract_bash_verb returns subcommand-aware verbs ("git diff"); match on the
    base token so git/python3/npm/etc. count as dev."""
    bases = [v.split()[0] for v in (_scan.extract_bash_verb(c) for c in cmds) if v]
    dev = sum(1 for b in bases if b in _scan.DEV_VERBS)
    if bases and dev >= max(2, len(bases) // 4):
        return "dev"
    return "other"


def scan_codex(root: Path, max_age_days: int, cwd: Path | None = None,
               budget: str | None = None) -> dict:
    sessions = [_scan_one(p) for p in _iter_rollouts(root, max_age_days)]
    session_count = len(sessions)

    bash_verbs = collections.Counter()
    bash_samples: dict[str, str] = {}
    tool_use = collections.Counter()
    mcp_calls = collections.Counter()
    prompts = collections.Counter()
    projects = collections.Counter()
    proj_tokens = collections.Counter()
    proj_cmds: dict[str, list] = collections.defaultdict(list)
    destructive = collections.Counter()
    destructive_sample: dict[str, str] = {}
    raw_http = collections.Counter()
    sleep_calls = 0
    exec_ok = exec_err = 0
    files_modified: set[str] = set()
    commits = pushes = 0

    for s in sessions:
        tool_use.update(s["tool_use"])
        mcp_calls.update(s["mcp_calls"])
        exec_ok += s["exec_ok"]; exec_err += s["exec_err"]
        files_modified |= s["files_modified"]
        commits += int(s["committed"]); pushes += int(s["pushed"])
        path = s["cwd"] or "(unknown)"
        projects[path] += 1
        proj_tokens[path] += s["tokens"]
        proj_cmds[path].extend(s["cmds"])
        for pr in s["prompts"]:
            prompts[_scan.normalize_prompt(pr)[:160]] += 1
        for c in s["cmds"]:
            v = _scan.extract_bash_verb(c)
            if v:
                bash_verbs[v] += 1
                bash_samples.setdefault(v, c[:200])
            for rx, label in _scan.DESTRUCTIVE_PATTERNS:
                if rx.search(c):
                    destructive[label] += 1
                    destructive_sample.setdefault(label, c[:160])
            mh = _scan.CURL_HOST_RE.search(c)
            if mh:
                raw_http[mh.group(1)] += 1
            if re.search(r"\bsleep\s+\d", c):
                sleep_calls += 1

    top_projects = []
    for path, n in projects.most_common(6):
        top_projects.append({
            "path": path, "sessions": n, "tokens": proj_tokens[path],
            "kind": _classify_kind(proj_cmds[path], []),
            "branch": None, "commits": 0, "pushes": 0,
        })
    dev = sum(1 for p in top_projects if p["kind"] == "dev")
    mix_dev = round(100 * dev / len(top_projects)) if top_projects else 0
    work_mix = {"dev": mix_dev, "writing": 0, "data": 0,
                "ops": 0, "other": 100 - mix_dev}

    catalogs = _scan.discover_catalogs()

    recurring = [{"prompt": p, "count": c} for p, c in prompts.most_common(12)
                 if c >= 2]

    return {
        "session_count": session_count,
        "max_age_days": max_age_days,
        "scanned_root": str(root),
        "source": "codex",
        "projects": dict(projects),
        "bash_verbs_top": dict(bash_verbs.most_common(12)),
        "bash_verb_samples": {k: bash_samples[k] for k, _ in bash_verbs.most_common(12)},
        "tool_use_top": dict(tool_use.most_common(12)),
        "mcp_calls_top": dict(mcp_calls.most_common(12)),
        "recurring_prompts": recurring,
        "sampled_oneoff_prompts": [],
        "session_index": [],
        "web_fetches": 0,
        "work_recap": {"top_projects": top_projects, "mix": work_mix},
        "coaching_signals": {
            # No built-in Grep/Glob/Read in Codex — everything is exec_command.
            # Zeroed so analyze treats "shell vs built-in" as no_data, not 100%.
            "native_tool_bypass": {"bash_total": 0, "bypass_total": 0,
                                   "bypass_calls": {}, "suggested_tool": {},
                                   "native_tool_use": {}},
            "destructive_cmds": [{"label": k, "count": v,
                                  "sample": destructive_sample.get(k, "")}
                                 for k, v in destructive.most_common()],
            "raw_http_hosts": dict(raw_http.most_common(10)),
            "sleep_calls": sleep_calls,
            "hot_repos_without_claudemd": [],
        },
        # No Anthropic outcome facets in Codex — empty coverage degrades cleanly.
        "outcomes": {"by_facet": {}, "friction_sessions": {},
                     "friction_counts_sum": {}, "primary_success_top": {},
                     "session_type_mix": {}, "helpfulness_mix": {},
                     "coverage": {"labeled": 0, "total": session_count}},
        "completion": {
            "sessions_with_commit": commits, "sessions_with_push": pushes,
            "lines_added": 0, "lines_removed": 0,
            "files_modified": len(files_modified),
            "prs_detected_via_gh": {}, "coverage": {"with_meta": 0, "total": session_count},
        },
        "tool_errors": {"Bash": {"ok": exec_ok, "error": exec_err}},
        "memory_events": {"remember_invocations": 0, "memory_file_edits": 0,
                          "sessions_with_memory": 0},
        "stuck_loops": [],
        "available_catalogs": catalogs,
        "installed_skills": [], "installed_plugins": [],
        "ignored_names": _scan.load_ignored(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan recent OpenAI Codex sessions.")
    ap.add_argument("--root", default=str(CODEX_HOME),
                    help="Path to ~/.codex (default).")
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--budget", choices=tuple(_scan.BUDGETS.keys()),
                    default=_scan.DEFAULT_BUDGET)
    ap.add_argument("--cwd", default=None)
    args = ap.parse_args()
    root = Path(args.root)
    if not root.is_dir():
        print(json.dumps({"error": f"Codex dir not found: {root}", "session_count": 0}))
        return 1
    summary = scan_codex(root, args.days, None, budget=args.budget)
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
