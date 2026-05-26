#!/usr/bin/env python3
"""
The Daimon Grove — conservative gamification on top of skills-daimon.

May celebrate, summarize, and motivate. May NEVER replace, exaggerate,
invent, or hide the evidence. Does not reward raw usage — only better
Claude Code habits visible in the existing scan output.

Public surface:
    build_game_state(analysis_payload, history_snapshots=None,
                     today=None, window_days=None) -> dict
    numeric_game_history_snapshot(game_state) -> dict

Both are pure: the same inputs always produce the same output. History is
read for delta computation only; same-day reruns do not double-award XP
because the prior comparison uses the most recent **distinct** date entry.

No raw prompts, commands, paths, repo names, session IDs, or secret-like
strings are emitted. Every user-visible string is a fixed template plus
numeric counts.
"""

from __future__ import annotations

# ─── Tunable constants (single source of truth) ────────────────────────────
GAME_SCHEMA_VERSION = 1

TRACKS = (
    "automation", "memory", "safety", "planning",
    "tool_fluency", "project_hygiene",
)

TRACK_DISPLAY_NAMES = {
    "automation": "Automation",
    "memory": "Memory",
    "safety": "Safety",
    "planning": "Planning",
    "tool_fluency": "Tool Fluency",
    "project_hygiene": "Project Hygiene",
}

GROVE_AREA_NAMES = {
    "automation": "Command Tree",
    "memory": "Memory Well",
    "safety": "Git Thorns",
    "planning": "Planning Path",
    "tool_fluency": "Tool Shrine",
    "project_hygiene": "Repo Signpost",
}

GROVE_POSITIVE_STATUS = {
    "automation": "Command Tree grew",
    "memory": "Memory Well filled",
    "safety": "Git Thorns receded",
    "planning": "Planning Path extended",
    "tool_fluency": "Tool Shrine brightened",
    "project_hygiene": "Repo Signpost appeared",
}

GROVE_STEADY_STATUS = {
    "automation": "Command Tree steady",
    "memory": "Memory Well steady",
    "safety": "Git Thorns steady",
    "planning": "Planning Path steady",
    "tool_fluency": "Tool Shrine steady",
    "project_hygiene": "Repo Signpost steady",
}

GROVE_NEEDS_DATA_STATUS = {
    "automation": "Command Tree needs data",
    "memory": "Memory Well needs data",
    "safety": "Git Thorns need data",
    "planning": "Planning Path needs data",
    "tool_fluency": "Tool Shrine needs data",
    "project_hygiene": "Repo Signpost needs data",
}

# Per-track XP awarded on a verified improvement. Capped per run (see below).
XP_AWARDS = {
    "automation":      35,
    "memory":          30,
    "safety":          25,
    "planning":        25,
    "project_hygiene": 25,
    "tool_fluency":    20,
}

# Hard ceiling on XP added per track per report run (prevents runaway).
MAX_DELTA_PER_TRACK = 50

REPEATED_PROMPT_THRESHOLD = 3
MIN_MEMORY_SESSIONS = 10
LOW_MEMORY_RATE_PCT = 10
MIN_LABELED_SESSIONS = 5
HIGH_SHELL_PROBING_PCT = 15
HIGH_BASH_ERROR_PCT = 10

# Level ladders. Level = count of thresholds the XP has reached.
# Same shape used for per-track and global Daimon Level.
TRACK_LEVEL_THRESHOLDS = (0, 30, 70, 130, 210, 310, 440, 600, 800)
DAIMON_LEVEL_THRESHOLDS = (0, 100, 250, 450, 700, 1000, 1400, 1900, 2500)

# Quest tie-break priority (highest first).
QUEST_PRIORITY = (
    "safety", "automation", "project_hygiene",
    "planning", "memory", "tool_fluency",
)

# Badges: fixed order. Stored as a numeric bitmask in history.
BADGE_ORDER = (
    "command_sapling",   # at least one repeated prompt verified-saved as a command
    "memory_keeper",     # 3+ verified reusable learnings saved
    "repo_warden",       # all active repos have CLAUDE.md
    "safe_hands",        # no risky git for 3 distinct windows
    "pathfinder",        # planning friction down across 3 distinct days
    "tool_adept",        # built-in tools usage exceeds shell file probing
)

BADGE_NAMES = {
    "command_sapling": "Command Sapling",
    "memory_keeper":   "Memory Keeper",
    "repo_warden":     "Repo Warden",
    "safe_hands":      "Safe Hands",
    "pathfinder":      "Pathfinder",
    "tool_adept":      "Tool Adept",
}

# Fixed template strings (privacy: no user content interpolates here other
# than numeric counts and well-known fixed tokens).
QUEST_DEFS = {
    "plant_command_tree": {
        "title": "Plant a Command Tree",
        "track": "automation",
        "why_tmpl": "A repeated prompt candidate appeared {count} times with no saved command.",
        "do": "Say: “turn my prompt into a /command.”",
        "reward": "+35 Automation XP when verified later.",
    },
    "fill_memory_well": {
        "title": "Fill the Memory Well",
        "track": "memory",
        "why_tmpl": "Memory activity was low across {sessions} observed sessions.",
        "do": "Say: “save what we learned.”",
        "reward": "+30 Memory XP when verified later.",
    },
    "clear_git_thorns": {
        "title": "Clear the Git Thorns",
        "track": "safety",
        "why_tmpl": "Risky git commands appeared {count} times this window.",
        "do": "Before destructive git commands, ask for a blast-radius and recovery plan.",
        "reward": "+25 Safety XP when verified later.",
    },
    "walk_planning_path": {
        "title": "Walk the Planning Path",
        "track": "planning",
        "why_tmpl": "wrong_approach appeared in {sessions} of {labeled} labeled sessions, with {events} events.",
        "do": "Before coding, ask for a 5-step plan and one risky assumption.",
        "reward": "+25 Planning XP when verified later.",
    },
    "place_repo_signpost": {
        "title": "Place the Repo Signpost",
        "track": "project_hygiene",
        "why_tmpl": "{missing} of {total} active repos are missing CLAUDE.md.",
        "do": "Add or update CLAUDE.md in one active repo.",
        "reward": "+25 Project Hygiene XP when verified later.",
    },
    "tune_tool_shrine": {
        "title": "Tune the Tool Shrine",
        "track": "tool_fluency",
        "why_tmpl": "Shell-based probing or bash errors were elevated this window.",
        "do": "Use the built-in file/search tools before falling back to shell probing.",
        "reward": "+20 Tool Fluency XP when verified later.",
    },
}


# ─── helpers ────────────────────────────────────────────────────────────────
def _level_for(xp: int, thresholds=TRACK_LEVEL_THRESHOLDS) -> int:
    """Number of thresholds ≤ xp. Deterministic and monotone in xp."""
    if not isinstance(xp, (int, float)) or xp < 0:
        return 0
    return sum(1 for t in thresholds if xp >= t)


def _safe_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _safe_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y"}
    return False


def display_track_name(track_id: str) -> str:
    """Human display label for internal track ids."""
    return TRACK_DISPLAY_NAMES.get(track_id, str(track_id or "").replace("_", " ").title())


def format_delta(delta: int, status: str | None = None) -> str:
    """User-facing delta label for the track table."""
    d = _safe_int(delta)
    if d > 0:
        return f"+{d} XP"
    if status == "needs_data":
        return "Needs data"
    if status == "not_scored":
        return "Not scored"
    return "No change"


def safe_rate_text(numerator: int, denominator: int, label: str) -> str:
    """Never render meaningless X% of 0 claims."""
    n = _safe_int(numerator)
    d = _safe_int(denominator)
    if d <= 0:
        return "Not enough session data to evaluate this area."
    pct = round(100 * n / d)
    return f"{label}: {pct}% ({n} of {d})."


def _nested_numeric(obj: dict, paths: tuple[tuple[str, ...], ...]) -> int:
    for path in paths:
        cur = obj
        ok = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                ok = False
                break
            cur = cur.get(key)
        if ok and isinstance(cur, (int, float)):
            return _safe_int(cur)
    return 0


def _prior_distinct(history_snapshots, today: str | None):
    """Most recent prior history entry with a date different from `today`."""
    if not history_snapshots:
        return None
    # Latest first
    sorted_snaps = sorted(history_snapshots, key=lambda s: s.get("date", ""), reverse=True)
    for s in sorted_snaps:
        if (today is None) or s.get("date") != today:
            return s
    return None


def _evidence_from_payload(p: dict) -> dict:
    """Extract the numeric signals gamify cares about. Returns plain ints/floats."""
    cs = p.get("coaching_signals") or {}
    nb = cs.get("native_tool_bypass") or {}
    bypass_total = _safe_int(nb.get("bypass_total"))
    native_total = sum(_safe_int(v) for v in (nb.get("native_tool_use") or {}).values())
    file_tool_total = bypass_total + native_total
    risky = sum(
        _safe_int(x.get("count")) for x in (cs.get("destructive_cmds") or [])
        if x.get("label") and x.get("label") != "rm -rf"
    )
    hot_no_cmd = cs.get("hot_repos_without_claudemd") or []
    claudemd_missing = len(hot_no_cmd)
    # active repos = projects shown in the recap (basenames). Use top_projects len as proxy.
    top_projects = (p.get("work_recap") or {}).get("top_projects") or []
    active_repos = sum(1 for tp in top_projects if (tp.get("kind") == "dev"))
    active_repo_total = max(active_repos, claudemd_missing)
    # outcomes
    outcomes = p.get("outcomes") or {}
    cov = outcomes.get("coverage") or {}
    labeled = _safe_int(cov.get("labeled"))
    total = _safe_int(cov.get("total"))
    session_count = _safe_int(p.get("session_count") or total)
    by_facet = outcomes.get("by_facet") or {}
    finished = _safe_int(by_facet.get("fully_achieved")) + _safe_int(by_facet.get("mostly_achieved"))
    fin_pct = round(100 * finished / labeled) if labeled else 0
    fric_sess = outcomes.get("friction_sessions") or {}
    fric_sum = outcomes.get("friction_counts_sum") or {}
    wa_sessions = _safe_int(fric_sess.get("wrong_approach"))
    wa_events = _safe_int(fric_sum.get("wrong_approach"))
    wa_pct = round(100 * wa_sessions / labeled) if labeled else 0
    top_friction_events = max((_safe_int(v) for v in fric_sum.values()), default=0)
    wrong_approach_is_top = bool(wa_events > 0 and wa_events >= top_friction_events)
    # tool errors
    te = p.get("tool_errors") or {}
    bash = te.get("Bash") or {}
    bash_ok = _safe_int(bash.get("ok"))
    bash_err = _safe_int(bash.get("error"))
    bash_total = bash_ok + bash_err
    bash_err_pct = round(100 * bash_err / bash_total, 1) if bash_total else 0.0
    # memory
    me = p.get("memory_events") or {}
    mem_sessions = _safe_int(me.get("sessions_with_memory"))
    mem_denominator = session_count
    mem_pct = round(100 * mem_sessions / mem_denominator) if mem_denominator else 0
    verified_saved_learnings = max(
        _nested_numeric(p, (
            ("memory_events", "verified_saved_learnings"),
            ("memory_events", "reusable_learnings_saved"),
            ("memory_events", "learnings_keeper_handoffs"),
            ("coaching_signals", "memory", "verified_saved_learnings"),
        )),
        _safe_int(me.get("remember_invocations")) + _safe_int(me.get("memory_file_edits")),
    )
    # recurring prompts (top one without a saved skill counterpart)
    recurring = p.get("recurring_prompts") or []
    top_recurring = max((_safe_int(r.get("count")) for r in recurring), default=0)
    installed = p.get("installed_skills") or []
    has_prompt_to_command = "prompt-to-command" in installed
    saved_command_count = _nested_numeric(p, (
        ("command_events", "saved_from_repeated_prompt"),
        ("command_events", "commands_created_from_repeated_prompts"),
        ("command_events", "saved_command_count"),
        ("coaching_signals", "saved_command_evidence", "count"),
        ("coaching_signals", "saved_commands_from_recurring_prompts"),
    ))
    command_reuse_detected = _safe_bool(
        ((p.get("command_events") or {}).get("command_reuse_detected"))
        or ((cs.get("saved_command_evidence") or {}).get("command_reuse_detected"))
    )
    # search shell vs built-in
    search_shell_pct = round(100 * bypass_total / file_tool_total) if file_tool_total else 0
    # stuck loops
    stuck = p.get("stuck_loops") or []
    stuck_count = len(stuck)

    return {
        "risky_git_count": risky,
        "claudemd_missing": claudemd_missing,
        "active_repos": active_repos,
        "active_repo_total": active_repo_total,
        "top_recurring": top_recurring,
        "has_prompt_to_command_skill": bool(has_prompt_to_command),
        "saved_command_count": saved_command_count,
        "command_reuse_detected": command_reuse_detected,
        "labeled": labeled,
        "total": total,
        "session_count": session_count,
        "finished_pct": fin_pct,
        "wa_sessions": wa_sessions,
        "wa_events": wa_events,
        "wa_pct": wa_pct,
        "wrong_approach_is_top": wrong_approach_is_top,
        "bash_err_pct": bash_err_pct,
        "bash_total": bash_total,
        "memory_rate_pct": mem_pct,
        "memory_sessions": mem_sessions,
        "memory_denominator": mem_denominator,
        "verified_saved_learnings": verified_saved_learnings,
        "search_shell_pct": search_shell_pct,
        "bypass_total": bypass_total,
        "native_total": native_total,
        "file_tool_total": file_tool_total,
        "stuck_count": stuck_count,
    }


def _select_quest(ev: dict, prior: dict | None = None) -> dict | None:
    """Pick one active quest using evidence presence + tie-break priority."""
    candidates: list[tuple[str, dict, dict]] = []
    prior_sc = (prior or {}).get("scorecard") or {}
    prior_game = (prior or {}).get("game") or {}

    # Safety
    if ev["risky_git_count"] >= 1:
        candidates.append(("safety", QUEST_DEFS["clear_git_thorns"], {
            "id": "clear_git_thorns",
            "evidence": {"risky_git_count": ev["risky_git_count"]},
            "why": QUEST_DEFS["clear_git_thorns"]["why_tmpl"].format(count=ev["risky_git_count"]),
        }))

    # Automation: a repeated prompt with no saved command counterpart yet.
    if ev["top_recurring"] >= REPEATED_PROMPT_THRESHOLD and ev["saved_command_count"] == 0:
        candidates.append(("automation", QUEST_DEFS["plant_command_tree"], {
            "id": "plant_command_tree",
            "evidence": {"repeated_prompt_count": ev["top_recurring"], "saved_command_detected": 0},
            "why": QUEST_DEFS["plant_command_tree"]["why_tmpl"].format(count=ev["top_recurring"]),
        }))

    # Project hygiene
    if ev["claudemd_missing"] >= 1:
        candidates.append(("project_hygiene", QUEST_DEFS["place_repo_signpost"], {
            "id": "place_repo_signpost",
            "evidence": {"missing_claudemd": ev["claudemd_missing"],
                          "active_repos": ev["active_repo_total"]},
            "why": QUEST_DEFS["place_repo_signpost"]["why_tmpl"].format(
                missing=ev["claudemd_missing"], total=ev["active_repo_total"]),
        }))

    # Planning
    prior_stuck = _safe_int(prior_game.get("rhythm_stuck_count")
                            or prior_sc.get("stuck_count"))
    stuck_increased = prior is not None and ev["stuck_count"] > prior_stuck
    if ev["labeled"] >= MIN_LABELED_SESSIONS and (
        ev["wrong_approach_is_top"] or stuck_increased
    ):
        candidates.append(("planning", QUEST_DEFS["walk_planning_path"], {
            "id": "walk_planning_path",
            "evidence": {"sessions": ev["wa_sessions"], "labeled": ev["labeled"],
                          "events": ev["wa_events"]},
            "why": QUEST_DEFS["walk_planning_path"]["why_tmpl"].format(
                sessions=ev["wa_sessions"], labeled=ev["labeled"], events=ev["wa_events"]),
        }))

    # Memory
    if ev["memory_denominator"] >= MIN_MEMORY_SESSIONS and (
        ev["memory_rate_pct"] < LOW_MEMORY_RATE_PCT or ev["verified_saved_learnings"] == 0
    ):
        candidates.append(("memory", QUEST_DEFS["fill_memory_well"], {
            "id": "fill_memory_well",
            "evidence": {"sessions_observed": ev["memory_denominator"],
                          "memory_rate_pct": ev["memory_rate_pct"]},
            "why": QUEST_DEFS["fill_memory_well"]["why_tmpl"].format(
                sessions=ev["memory_denominator"]),
        }))

    # Tool fluency
    bash_error_increased = (
        prior is not None
        and ev["bash_total"] > 0
        and "bash_error_pct" in prior_sc
        and ev["bash_err_pct"] > prior_sc["bash_error_pct"]
    )
    if (
        (ev["file_tool_total"] > 0 and ev["search_shell_pct"] >= HIGH_SHELL_PROBING_PCT)
        or ev["bash_err_pct"] >= HIGH_BASH_ERROR_PCT
        or bash_error_increased
    ):
        candidates.append(("tool_fluency", QUEST_DEFS["tune_tool_shrine"], {
            "id": "tune_tool_shrine",
            "evidence": {"search_shell_pct": ev["search_shell_pct"],
                          "bash_err_pct": ev["bash_err_pct"]},
            "why": QUEST_DEFS["tune_tool_shrine"]["why_tmpl"],
        }))

    if not candidates:
        return None

    # Tie-break: order by QUEST_PRIORITY, then deterministic by quest id.
    priority = {t: i for i, t in enumerate(QUEST_PRIORITY)}
    candidates.sort(key=lambda t: (priority.get(t[0], 99), t[2]["id"]))
    track, defn, data = candidates[0]
    return {
        "id": data["id"],
        "title": defn["title"],
        "track": track,
        "why": data["why"],
        "do": defn["do"],
        "reward": defn["reward"],
        "evidence": data["evidence"],
    }


def choose_active_quest(analysis_payload: dict, history_snapshots: list[dict] | None = None,
                        today: str | None = None) -> dict | None:
    """Public helper: exactly one evidence-backed quest, or None."""
    ev = _evidence_from_payload(analysis_payload or {})
    prior = _prior_distinct(history_snapshots, today)
    return _select_quest(ev, prior)


def _compute_xp_deltas(ev: dict, prior: dict | None) -> dict:
    """Award XP only on verified improvement vs `prior`. Always ≥ 0, capped."""
    deltas = {t: 0 for t in TRACKS}
    if not prior:
        # No prior distinct day → nothing to verify yet; no XP awarded.
        return deltas

    prior_sc = prior.get("scorecard") or {}
    prior_game = prior.get("game") or {}

    # Safety: risky_git_count dropped.
    if ev["session_count"] > 0 and "risky_git_count" in prior_sc:
        if ev["risky_git_count"] < prior_sc["risky_git_count"]:
            deltas["safety"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["safety"])

    # Tool fluency: bash error rate down OR shell-search % down.
    if (
        ev["bash_total"] > 0
        and "bash_error_pct" in prior_sc
        and ev["bash_err_pct"] < prior_sc["bash_error_pct"]
    ):
        deltas["tool_fluency"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["tool_fluency"])
    elif (
        ev["file_tool_total"] > 0
        and "search_shell_pct" in prior_sc
        and ev["search_shell_pct"] < prior_sc["search_shell_pct"]
    ):
        deltas["tool_fluency"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["tool_fluency"])

    # Memory: rate improved and current run has verified saved-learning evidence,
    # or verified saved-learning count increased across numeric history.
    prior_saved_learnings = _safe_int(prior_game.get("rhythm_saved_learnings"))
    if (
        ev["memory_denominator"] > 0
        and ev["verified_saved_learnings"] > 0
        and "memory_rate_pct" in prior_sc
        and ev["memory_rate_pct"] > prior_sc["memory_rate_pct"]
    ):
        deltas["memory"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["memory"])
    elif (
        "rhythm_saved_learnings" in prior_game
        and ev["verified_saved_learnings"] > prior_saved_learnings
    ):
        deltas["memory"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["memory"])

    # Planning: wrong_approach friction decreased across comparable labeled data,
    # or stuck loops decreased when that numeric rhythm exists.
    prior_wa_pct = prior_sc.get("wrong_approach_pct")
    if prior_wa_pct is None:
        prior_wa_pct = prior_game.get("rhythm_wrong_approach_pct")
    prior_labeled = _safe_int(prior.get("labeled") or prior_game.get("rhythm_labeled_sessions"))
    prior_stuck = prior_game.get("rhythm_stuck_count")
    if (
        isinstance(prior_wa_pct, (int, float))
        and ev["labeled"] >= MIN_LABELED_SESSIONS
        and prior_labeled >= MIN_LABELED_SESSIONS
        and ev["wa_pct"] < prior_wa_pct
    ):
        deltas["planning"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["planning"])
    elif (
        prior_stuck is not None
        and ev["labeled"] >= MIN_LABELED_SESSIONS
        and ev["stuck_count"] < _safe_int(prior_stuck)
    ):
        deltas["planning"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["planning"])

    # Project hygiene: number of repos missing CLAUDE.md dropped.
    if (
        ev["active_repo_total"] > 0
        and "claudemd_missing" in prior_sc
        and ev["claudemd_missing"] < prior_sc["claudemd_missing"]
    ):
        deltas["project_hygiene"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["project_hygiene"])

    # Automation: repeated prompt decreased AND a saved command was detected,
    # or command reuse was explicitly detected after a prior repeated prompt.
    prior_unsaved = prior_sc.get("unsaved_prompts")
    if (
        prior_unsaved is not None
        and prior_unsaved > 0
        and ev["saved_command_count"] > 0
        and ev["top_recurring"] < prior_unsaved
    ):
        deltas["automation"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["automation"])
    elif (
        prior_unsaved is not None
        and prior_unsaved > 0
        and ev["command_reuse_detected"]
    ):
        deltas["automation"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["automation"])

    return deltas


def _badge_state(ev: dict, history_snapshots: list[dict] | None,
                 today: str | None = None) -> list[dict]:
    """Conservative badges. Each is earned only when evidence is clear."""
    by_date: dict[str, dict] = {}
    for snap in sorted(history_snapshots or [], key=lambda s: s.get("date", "")):
        d = snap.get("date", "")
        if d and d != today:
            by_date[d] = snap
    snaps = [by_date[d] for d in sorted(by_date)]
    prior = snaps[-1] if snaps else None

    badges = []

    # Repo Warden: active repo data exists and every active repo has CLAUDE.md.
    badges.append({
        "id": "repo_warden",
        "name": BADGE_NAMES["repo_warden"],
        "earned": ev["active_repo_total"] > 0 and ev["claudemd_missing"] == 0,
        "evidence": "All active dev repos have CLAUDE.md coverage."
                    if ev["active_repo_total"] > 0 and ev["claudemd_missing"] == 0
                    else "CLAUDE.md coverage is not complete or active repo data is missing.",
    })

    # Safe Hands: this run + two prior distinct windows had zero risky git.
    safe_runs = snaps[-2:] if len(snaps) >= 2 else None
    safe_hands = (
        safe_runs is not None
        and ev["risky_git_count"] == 0
        and all(_safe_int((s.get("scorecard") or {}).get("risky_git_count")) == 0 for s in safe_runs)
    )
    badges.append({
        "id": "safe_hands",
        "name": BADGE_NAMES["safe_hands"],
        "earned": bool(safe_hands),
        "evidence": "No risky git commands across the last 3 report windows."
                    if safe_hands else "Risky git commands appeared in recent windows.",
    })

    # Pathfinder: wrong_approach friction decreased across three distinct days.
    pathfinder = False
    if ev["labeled"] >= MIN_LABELED_SESSIONS:
        prior_points: list[tuple[str, int]] = []
        for s in snaps:
            sc = s.get("scorecard") or {}
            game = s.get("game") or {}
            pct = sc.get("wrong_approach_pct")
            if pct is None:
                pct = game.get("rhythm_wrong_approach_pct")
            labeled = _safe_int(s.get("labeled") or game.get("rhythm_labeled_sessions"))
            if isinstance(pct, (int, float)) and labeled >= MIN_LABELED_SESSIONS:
                prior_points.append((s.get("date", ""), _safe_int(pct)))
        series = [p[1] for p in prior_points[-2:]] + [ev["wa_pct"]]
        pathfinder = len(series) == 3 and series[0] > series[1] > series[2]
    badges.append({
        "id": "pathfinder",
        "name": BADGE_NAMES["pathfinder"],
        "earned": bool(pathfinder),
        "evidence": "wrong_approach friction decreased across 3 distinct report days."
                    if pathfinder else "Planning friction trend is not yet established.",
    })

    # Tool Adept: built-in tools usage exceeds shell-based file probing.
    tool_adept = ev["file_tool_total"] > 0 and ev["native_total"] > ev["bypass_total"]
    badges.append({
        "id": "tool_adept",
        "name": BADGE_NAMES["tool_adept"],
        "earned": bool(tool_adept),
        "evidence": "Built-in tools were used more than shell-based file probing."
                    if tool_adept else "Shell-based file probing is still notable.",
    })

    # Command Sapling: a repeated prompt was verified as saved into a /command.
    sapling = False
    if prior:
        prior_unsaved = _safe_int((prior.get("scorecard") or {}).get("unsaved_prompts"))
        sapling = (
            ev["saved_command_count"] > 0
            and prior_unsaved > 0
            and (ev["top_recurring"] < prior_unsaved or ev["command_reuse_detected"])
        )
    badges.append({
        "id": "command_sapling",
        "name": BADGE_NAMES["command_sapling"],
        "earned": bool(sapling),
        "evidence": "A repeated prompt was verified as saved into a /command." if sapling
                    else "No verified command-from-prompt yet.",
    })

    # Memory Keeper: 3+ verified saved learnings.
    keeper = ev["verified_saved_learnings"] >= 3
    badges.append({
        "id": "memory_keeper",
        "name": BADGE_NAMES["memory_keeper"],
        "earned": bool(keeper),
        "evidence": "At least 3 reusable learnings were verified as saved." if keeper
                    else "Fewer than 3 verified saved learnings.",
    })

    return badges


def badge_unlocks_from_evidence(analysis_payload: dict,
                                history_snapshots: list[dict] | None = None,
                                today: str | None = None) -> list[dict]:
    """Public helper for conservative badge computation."""
    return _badge_state(_evidence_from_payload(analysis_payload or {}),
                        history_snapshots, today)


def _badges_mask(badges: list[dict]) -> int:
    """Numeric encoding of earned badges (bit position = BADGE_ORDER index)."""
    mask = 0
    earned = {b["id"]: b["earned"] for b in badges}
    for i, bid in enumerate(BADGE_ORDER):
        if earned.get(bid):
            mask |= (1 << i)
    return mask


def _track_status(track: str, ev: dict, has_prior: bool, delta: int) -> str:
    """Scoring state for delta display."""
    if delta > 0:
        return "verified"
    if track == "memory" and ev["memory_denominator"] <= 0:
        return "needs_data"
    if track == "planning" and ev["labeled"] < MIN_LABELED_SESSIONS:
        return "needs_data"
    if track == "tool_fluency" and ev["file_tool_total"] <= 0 and ev["bash_total"] <= 0:
        return "needs_data"
    if track == "project_hygiene" and ev["active_repo_total"] <= 0:
        return "needs_data"
    if track == "automation" and ev["session_count"] <= 0:
        return "needs_data"
    if track == "safety" and ev["session_count"] <= 0:
        return "needs_data"
    if not has_prior:
        return "not_scored"
    return "no_change"


def _track_evidence_one_liner(track: str, ev: dict, delta: int, status: str) -> str:
    """Fixed-template evidence line per track. No raw user content."""
    if track == "automation":
        if delta > 0:
            return f"+{delta} Automation XP — repeated prompt was verified as saved into a command."
        if status == "needs_data":
            return "Not enough session data to evaluate this area."
        return f"Top repeated prompt seen {ev['top_recurring']} times this window."
    if track == "memory":
        if delta > 0:
            return f"+{delta} Memory XP — reusable learning activity improved and was verified."
        if status == "needs_data":
            return "Not enough session data to evaluate this area."
        return safe_rate_text(ev["memory_sessions"], ev["memory_denominator"], "Memory activity")
    if track == "safety":
        if delta > 0:
            return f"+{delta} Safety XP — risky git events decreased to {ev['risky_git_count']} this window."
        if status == "needs_data":
            return "Not enough session data to evaluate this area."
        return f"Risky git events: {ev['risky_git_count']} this window."
    if track == "planning":
        if delta > 0:
            return f"+{delta} Planning XP — wrong_approach friction decreased across comparable labeled sessions."
        if status == "needs_data":
            return "Not enough labeled sessions for a planning read."
        return f"wrong_approach detected in {ev['wa_sessions']} of {ev['labeled']} labeled sessions."
    if track == "tool_fluency":
        if delta > 0:
            return f"+{delta} Tool Fluency XP — built-in tool use improved against shell-based probing or Bash errors decreased."
        if status == "needs_data":
            return "Not enough tool data to evaluate this area."
        return f"Shell-based file probing: {ev['search_shell_pct']}% of file searches."
    if track == "project_hygiene":
        if delta > 0:
            return f"+{delta} Project Hygiene XP — CLAUDE.md coverage improved."
        if status == "needs_data":
            return "Not enough active repo data to evaluate this area."
        return f"{ev['claudemd_missing']} active repos missing CLAUDE.md."
    return ""


def _join_sentence(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def build_grove_summary(game_state: dict) -> dict:
    """Render-safe plain-English summary of what changed this run."""
    gs = game_state or {}
    tracks = gs.get("tracks") or {}
    positive = [
        GROVE_POSITIVE_STATUS[t]
        for t in TRACKS
        if _safe_int((tracks.get(t) or {}).get("delta")) > 0
    ]
    needs = [
        GROVE_NEEDS_DATA_STATUS[t]
        for t in TRACKS
        if (tracks.get(t) or {}).get("status") == "needs_data"
    ]
    labels = []
    for t in TRACKS:
        tr = tracks.get(t) or {}
        if _safe_int(tr.get("delta")) > 0:
            labels.append(GROVE_POSITIVE_STATUS[t])
        elif tr.get("status") == "needs_data":
            labels.append(GROVE_NEEDS_DATA_STATUS[t])
        else:
            labels.append(GROVE_STEADY_STATUS[t])

    if positive:
        sentence = "Your " + _join_sentence(positive) + "."
    elif needs:
        sentence = _join_sentence(needs) + "."
    else:
        sentence = "No grove areas changed this run; XP waits for verified habit improvement."

    quest = gs.get("active_quest")
    if quest:
        next_quest = f"Next mission: {quest.get('title', '')}."
    else:
        next_quest = "No mission this run. The report did not find a strong enough evidence-backed opportunity."

    return {
        "xp_delta_total": sum(_safe_int((tracks.get(t) or {}).get("delta")) for t in TRACKS),
        "change_sentence": sentence,
        "status_labels": labels,
        "next_quest": next_quest,
    }


# ─── public API ─────────────────────────────────────────────────────────────
def build_game_state(
    analysis_payload: dict,
    history_snapshots: list[dict] | None = None,
    today: str | None = None,
    window_days: int | None = None,
) -> dict:
    """Compute the full game state from the existing analysis payload.

    `history_snapshots` is an optional list of numeric-only history entries
    (the same shape this module writes via `numeric_game_history_snapshot`).
    `today` is the ISO date for "this run"; same-day reruns dedupe against it.
    """
    ev = _evidence_from_payload(analysis_payload)
    prior = _prior_distinct(history_snapshots, today)
    prior_game = (prior.get("game") if prior else None) or {}

    # XP deltas (per track) for verified improvement vs prior distinct day.
    deltas = _compute_xp_deltas(ev, prior)
    # Cumulative track XP.
    tracks_xp: dict[str, int] = {}
    for t in TRACKS:
        prior_xp = _safe_int(prior_game.get(f"xp_{t}"))
        d = max(0, deltas.get(t, 0))
        d = min(d, MAX_DELTA_PER_TRACK)
        tracks_xp[t] = prior_xp + d

    xp_total = sum(tracks_xp.values())
    daimon_level = _level_for(xp_total, DAIMON_LEVEL_THRESHOLDS)

    # Pick the active quest from this run's evidence.
    quest = _select_quest(ev, prior)

    # Build per-track structure for the report.
    tracks_out: dict[str, dict] = {}
    has_prior = prior is not None
    for t in TRACKS:
        status = _track_status(t, ev, has_prior, deltas[t])
        tracks_out[t] = {
            "id": t,
            "name": display_track_name(t),
            "grove_area": GROVE_AREA_NAMES[t],
            "xp": tracks_xp[t],
            "level": _level_for(tracks_xp[t], TRACK_LEVEL_THRESHOLDS),
            "delta": deltas[t],
            "status": status,
            "delta_label": format_delta(deltas[t], status),
            "evidence": _track_evidence_one_liner(t, ev, deltas[t], status),
        }

    # Badges (state + numeric mask for history).
    badges = _badge_state(ev, history_snapshots, today)
    badges_mask = _badges_mask(badges)
    badge_count = sum(1 for b in badges if b["earned"])

    grove = {
        "level": daimon_level,
        "command_tree_level":  tracks_out["automation"]["level"],
        "memory_well_level":   tracks_out["memory"]["level"],
        "git_thorn_level":     tracks_out["safety"]["level"],
        "planning_path_level": tracks_out["planning"]["level"],
        "tool_shrine_level":   tracks_out["tool_fluency"]["level"],
        "repo_signpost_level": tracks_out["project_hygiene"]["level"],
        # A constellation lights up once trends are real (≥3 distinct days
        # of numeric history).
        "constellation_unlocked": (history_snapshots is not None and
                                    len({s.get("date") for s in history_snapshots}) >= 3),
    }

    rhythms = {
        "wrong_approach_pct": ev["wa_pct"],
        "labeled_sessions": ev["labeled"],
        "stuck_count": ev["stuck_count"],
        "risky_git_count": ev["risky_git_count"],
        "saved_learnings": ev["verified_saved_learnings"],
        "saved_commands": ev["saved_command_count"],
        "active_repo_total": ev["active_repo_total"],
    }

    # Cumulative quest counts (numeric only).
    prior_quests_offered = _safe_int(prior_game.get("quests_offered_count"))
    prior_quests_completed = _safe_int(prior_game.get("quests_completed_count"))
    quests_offered = prior_quests_offered + (1 if quest else 0)
    # A quest is "completed" when XP was awarded in its track since the prior
    # distinct day. Approximate: any delta > 0 anywhere → +1 completion.
    quests_completed = prior_quests_completed + (1 if any(d > 0 for d in deltas.values()) else 0)

    state = {
        "game_schema_version": GAME_SCHEMA_VERSION,
        "xp_total": xp_total,
        "daimon_level": daimon_level,
        "tracks": tracks_out,
        "active_quest": quest,
        "no_quest_reason": (
            None if quest else
            "No mission this run. The report did not find a strong enough evidence-backed opportunity."
        ),
        "badges": badges,
        "badges_mask": badges_mask,
        "badge_count": badge_count,
        "grove": grove,
        "rhythms": rhythms,
        "quests_offered_count": quests_offered,
        "quests_completed_count": quests_completed,
    }
    state["grove_summary"] = build_grove_summary(state)
    return state


def numeric_game_history_snapshot(game_state: dict) -> dict:
    """Numeric-only fields safe to append to history.jsonl as `game: {...}`."""
    g = game_state or {}
    out = {
        "game_schema_version": _safe_int(g.get("game_schema_version") or GAME_SCHEMA_VERSION),
        "xp_total": _safe_int(g.get("xp_total")),
        "daimon_level": _safe_int(g.get("daimon_level")),
        "badge_count": _safe_int(g.get("badge_count")),
        "badges_mask": _safe_int(g.get("badges_mask")),
        "quests_offered_count": _safe_int(g.get("quests_offered_count")),
        "quests_completed_count": _safe_int(g.get("quests_completed_count")),
    }
    for t in TRACKS:
        tr = (g.get("tracks") or {}).get(t) or {}
        out[f"xp_{t}"] = _safe_int(tr.get("xp"))
        out[f"level_{t}"] = _safe_int(tr.get("level"))
        out[f"delta_{t}"] = _safe_int(tr.get("delta"))
    grove = g.get("grove") or {}
    for k in ("level", "command_tree_level", "memory_well_level", "git_thorn_level",
              "planning_path_level", "tool_shrine_level", "repo_signpost_level"):
        out[f"grove_{k}"] = _safe_int(grove.get(k))
    out["constellation"] = 1 if grove.get("constellation_unlocked") else 0
    rhythms = g.get("rhythms") or {}
    for k in ("wrong_approach_pct", "labeled_sessions", "stuck_count",
              "risky_git_count", "saved_learnings", "saved_commands",
              "active_repo_total"):
        out[f"rhythm_{k}"] = _safe_int(rhythms.get(k))
    return out


# ─── deterministic inline SVG (privacy-safe, no external assets) ────────────
def render_grove_svg(grove: dict, *, width: int = 900, height: int = 320) -> str:
    """Return a self-contained inline SVG realm map of the Daimon Grove.

    Six sites are connected by a deterministic trail. No external images, no
    JS. Includes <title>/<desc> for accessibility.
    """
    g = grove or {}
    items = [
        ("Command Tree",   _safe_int(g.get("command_tree_level")),  "tree",   120, 220),
        ("Memory Well",    _safe_int(g.get("memory_well_level")),   "well",   260, 126),
        ("Git Thorns",     _safe_int(g.get("git_thorn_level")),     "thorns", 420, 216),
        ("Planning Path",  _safe_int(g.get("planning_path_level")), "path",   560, 116),
        ("Tool Shrine",    _safe_int(g.get("tool_shrine_level")),   "shrine", 708, 208),
        ("Repo Signpost",  _safe_int(g.get("repo_signpost_level")), "post",   810, 116),
    ]
    constellation = bool(g.get("constellation_unlocked"))
    site_fill = "#FFF7ED"
    site_stroke = "#B08968"
    ink = "#3F3A34"
    muted = "#6B7280"
    purple = "#6D28D9"
    green = "#0F766E"
    amber = "#B45309"
    red = "#B42318"
    blue = "#2563EB"

    def _label(x, y, name, level):
        return (
            f'<text x="{x}" y="{y+58}" text-anchor="middle" '
            f'font-size="13" font-weight="700" fill="{ink}" font-family="-apple-system,sans-serif">'
            f'{name}</text>'
            f'<text x="{x}" y="{y+75}" text-anchor="middle" '
            f'font-size="11" fill="{muted}" font-family="-apple-system,sans-serif">'
            f'Level {level}</text>'
        )

    def _site(cx, cy, lvl, body):
        ring = 28 + min(max(lvl - 1, 0), 5) * 2
        return (
            f'<circle cx="{cx}" cy="{cy}" r="{ring + 8}" fill="#FDECC8" opacity="0.85"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{ring}" fill="{site_fill}" stroke="{site_stroke}" stroke-width="3"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{ring - 7}" fill="none" stroke="#FCD34D" stroke-width="2" stroke-dasharray="3 7"/>'
            f'{body}'
        )

    glyphs = []
    for name, lvl, kind, cx, cy in items:
        a = max(1, min(6, lvl))
        if kind == "tree":
            body = (
                f'<rect x="{cx-4}" y="{cy-2}" width="8" height="30" rx="2" fill="#7C4A28"/>'
                f'<circle cx="{cx}" cy="{cy-18}" r="{16+a}" fill="{green}" opacity="0.95"/>'
                f'<circle cx="{cx-14}" cy="{cy-8}" r="{11+a}" fill="#10B981" opacity="0.88"/>'
                f'<circle cx="{cx+14}" cy="{cy-8}" r="{11+a}" fill="#047857" opacity="0.88"/>'
            )
        elif kind == "well":
            fill_h = 8 + a * 2
            body = (
                f'<ellipse cx="{cx}" cy="{cy+4}" rx="22" ry="11" fill="#A7F3D0" stroke="{blue}" stroke-width="3"/>'
                f'<rect x="{cx-22}" y="{cy-10}" width="44" height="22" fill="#FDE68A" stroke="{site_stroke}" stroke-width="2"/>'
                f'<rect x="{cx-18}" y="{cy+11-fill_h}" width="36" height="{fill_h}" fill="#60A5FA" opacity="0.72"/>'
                f'<path d="M {cx-24} {cy-10} Q {cx} {cy-34} {cx+24} {cy-10}" fill="none" stroke="{site_stroke}" stroke-width="3"/>'
            )
        elif kind == "thorns":
            thorns = max(1, 7 - a)
            bits = []
            for k in range(thorns):
                px = cx - 24 + k * 8
                bits.append(
                    f'<path d="M {px} {cy+18} L {px+4} {cy-12} L {px+8} {cy+18} Z" fill="{red}" opacity="0.9"/>'
                )
            body = "".join(bits) + (
                f'<path d="M {cx-32} {cy+20} C {cx-10} {cy+6}, {cx+10} {cy+6}, {cx+32} {cy+20}" '
                f'fill="none" stroke="#7F1D1D" stroke-width="3"/>'
            )
        elif kind == "path":
            stones = []
            for k in range(5):
                stones.append(
                    f'<ellipse cx="{cx-28+k*14}" cy="{cy+8-(k%2)*8}" rx="{5+a*0.4:.1f}" ry="4" fill="{purple}" opacity="0.9"/>'
                )
            body = "".join(stones) + (
                f'<path d="M {cx-34} {cy+24} C {cx-6} {cy-24}, {cx+12} {cy-20}, {cx+34} {cy+20}" '
                f'fill="none" stroke="{purple}" stroke-width="3" stroke-dasharray="5 5"/>'
            )
        elif kind == "shrine":
            body = (
                f'<rect x="{cx-28}" y="{cy+15}" width="56" height="7" rx="2" fill="{purple}"/>'
                f'<rect x="{cx-22}" y="{cy-8}" width="7" height="24" fill="{purple}"/>'
                f'<rect x="{cx-4}" y="{cy-8}" width="8" height="24" fill="{purple}" opacity="0.9"/>'
                f'<rect x="{cx+15}" y="{cy-8}" width="7" height="24" fill="{purple}"/>'
                f'<polygon points="{cx-34},{cy-8} {cx+34},{cy-8} {cx},{cy-28}" fill="{purple}"/>'
                f'<circle cx="{cx}" cy="{cy-38}" r="{4+a}" fill="#FCD34D"/>'
            )
        else:
            body = (
                f'<rect x="{cx-4}" y="{cy-24}" width="8" height="50" rx="3" fill="#7C4A28"/>'
                f'<path d="M {cx-32} {cy-20} H {cx+28} L {cx+18} {cy-8} H {cx-32} Z" fill="{amber}"/>'
                f'<path d="M {cx+30} {cy+2} H {cx-28} L {cx-18} {cy+14} H {cx+30} Z" fill="#F59E0B"/>'
            )
        glyphs.append(_site(cx, cy, lvl, body))
        # accessibility: per-glyph title for hover/AT
        glyphs.append(f'<title>{name} — level {lvl}</title>')
        glyphs.append(_label(cx, cy, name, lvl))

    # Constellation: small stars across the top when unlocked
    if constellation:
        stars = []
        for k, x in enumerate((124, 212, 326, 496, 646, 760, 832)):
            stars.append(
                f'<path d="M {x} 38 l4 9 10 1 -8 6 2 10 -8-5 -8 5 2-10 -8-6 10-1 Z" '
                f'fill="#FCD34D" stroke="#B45309" stroke-width="1" opacity="{0.75 + (k%2)*0.15}"/>'
            )
    else:
        stars = []

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMinYMin meet" '
        f'role="img" aria-label="Daimon Grove adventure map">'
        f'<title>Daimon Grove</title>'
        f'<desc>RPG-style adventure map with six craft sites: Command Tree, Memory Well, Git Thorns, '
        f'Planning Path, Tool Shrine, and Repo Signpost. Site level follows verified evidence.</desc>'
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="22" fill="#FFF8E7"/>'
        f'<path d="M 0 255 C 130 225, 190 286, 330 248 S 590 224, 900 258 V 320 H 0 Z" fill="#E7F6DC"/>'
        f'<path d="M 0 64 C 106 38, 214 54, 318 34 S 504 52, 630 34 S 786 48, 900 28 V 0 H 0 Z" fill="#FDECC8"/>'
        f'<path d="M 38 280 C 136 230, 192 172, 260 126 S 352 146, 420 216 S 500 165, 560 116 '
        f'S 650 162, 708 208 S 770 158, 810 116" fill="none" stroke="#C08457" stroke-width="13" '
        f'stroke-linecap="round" opacity="0.35"/>'
        f'<path d="M 38 280 C 136 230, 192 172, 260 126 S 352 146, 420 216 S 500 165, 560 116 '
        f'S 650 162, 708 208 S 770 158, 810 116" fill="none" stroke="#7C4A28" stroke-width="3" '
        f'stroke-linecap="round" stroke-dasharray="8 10" opacity="0.55"/>'
        f'<text x="34" y="38" font-size="18" font-weight="800" fill="{ink}" font-family="-apple-system,sans-serif">Daimon Grove Adventure Map</text>'
        f'<text x="34" y="58" font-size="12" fill="{muted}" font-family="-apple-system,sans-serif">Each landmark is a habit area. It changes only when evidence proves improvement.</text>'
        + "".join(stars)
        + "".join(glyphs)
        + '</svg>'
    )


__all__ = [
    "GAME_SCHEMA_VERSION", "TRACKS", "QUEST_DEFS", "BADGE_ORDER",
    "display_track_name", "format_delta", "safe_rate_text",
    "build_grove_summary", "choose_active_quest", "badge_unlocks_from_evidence",
    "build_game_state", "numeric_game_history_snapshot", "render_grove_svg",
]
