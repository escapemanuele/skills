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
    "balanced_grove",    # every track is ≥ Level 2 — see "constellation lights up"
)

BADGE_NAMES = {
    "command_sapling": "Command Sapling",
    "memory_keeper":   "Memory Keeper",
    "repo_warden":     "Repo Warden",
    "safe_hands":      "Safe Hands",
    "pathfinder":      "Pathfinder",
    "tool_adept":      "Tool Adept",
    "balanced_grove":  "Balanced Grove",
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
    # Balanced Grove — every track ≥ Level 2. Computed here because it depends
    # on the per-track levels, which are only known after deltas + XP roll-up.
    min_track_level = min(int(t.get("level") or 0) for t in tracks_out.values())
    badges.append({
        "id": "balanced_grove",
        "name": BADGE_NAMES["balanced_grove"],
        "earned": min_track_level >= 2,
        "evidence": ("Every track is at Level 2 or higher — the grove's night sky lights up."
                     if min_track_level >= 2
                     else f"Weakest track is at Level {min_track_level}; balance unlocks at L2 across all six."),
    })
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
        # The night sky payoff. Stars fade in per track that reaches Level 2;
        # when EVERY track is ≥ L2, the sky fully lights up (Balanced Grove).
        "tracks_at_l2_or_more": sum(
            1 for t in tracks_out.values() if int(t.get("level") or 0) >= 2
        ),
        "balanced": min_track_level >= 2,
        # A constellation lights up once trends are real (≥3 distinct days
        # of numeric history). Kept for back-compat with the old field name.
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
def render_grove_svg(grove: dict, *, width: int = 900, height: int = 280) -> str:
    """Return a self-contained inline SVG village-plot view of the Daimon Grove.

    Six sites share a single landscape — no path between them. Each site's glyph
    encodes its level (tree foliage, well fill, thorns count, stepping-stone count,
    shrine columns, signpost arms). When every track reaches Level 2, the sky
    transitions to evening and a soft constellation lights up overhead.
    """
    g = grove or {}
    items = [
        ("Command Tree",  _safe_int(g.get("command_tree_level")),  "tree",    90),
        ("Memory Well",   _safe_int(g.get("memory_well_level")),   "well",   235),
        ("Git Thorns",    _safe_int(g.get("git_thorn_level")),     "thorns", 380),
        ("Planning Path", _safe_int(g.get("planning_path_level")), "path",   525),
        ("Tool Shrine",   _safe_int(g.get("tool_shrine_level")),   "shrine", 670),
        ("Repo Signpost", _safe_int(g.get("repo_signpost_level")), "post",   815),
    ]
    # Vertical anchors (height=280). Ground band ~y180–y280, glyphs ~y130–y215.
    GROUND_Y = 215   # shadow baseline (also approx bottom of glyphs)
    LABEL_Y  = 240
    SUB_Y    = 256

    tracks_lit = max(0, min(6, _safe_int(g.get("tracks_at_l2_or_more"))))
    balanced = bool(g.get("balanced"))
    # night blend: 0.0 = full day, 1.0 = full night (only when balanced)
    night = 1.0 if balanced else 0.0

    # Text + accent palette flips for night so labels stay readable on dark sky.
    if balanced:
        ink = "#FEF3C7"     # warm cream — high contrast on indigo
        muted = "#CBBFA0"
        red = "#FCA5A5"     # bright red for "needs clearing"
    else:
        ink = "#2F271F"
        muted = "#4B5563"
        red = "#B42318"
    purple = "#6D28D9"
    green = "#0F766E"
    amber = "#B45309"
    blue = "#2563EB"
    site_stroke = "#B08968"

    def _label(cx, name, level, sub, *, warn=False):
        sub_fill = red if warn else muted
        sub_weight = ' font-weight="700"' if warn else ''
        return (
            f'<text x="{cx}" y="{LABEL_Y}" text-anchor="middle" font-size="13" font-weight="700" '
            f'fill="{ink}" font-family="-apple-system,sans-serif">{name}</text>'
            f'<text x="{cx}" y="{SUB_Y}" text-anchor="middle" font-size="11" fill="{sub_fill}"{sub_weight} '
            f'font-family="-apple-system,sans-serif">Level {level} · {sub}</text>'
        )

    def _shadow(cx, rx):
        return f'<ellipse cx="{cx}" cy="{GROUND_Y}" rx="{rx}" ry="6" fill="#000" opacity="0.10"/>'

    glyphs = []
    for name, lvl, kind, cx in items:
        a = max(1, min(6, lvl))
        if kind == "tree":
            # L0 sprout → L6 large tree. Trunk + crown scale dramatically by level.
            if lvl <= 0:
                # tiny two-leaf sprout poking out of soil
                body = (
                    _shadow(cx, 14)
                    + f'<rect x="{cx-1}" y="{GROUND_Y-10}" width="2" height="10" fill="#7C4A28"/>'
                    + f'<ellipse cx="{cx-4}" cy="{GROUND_Y-10}" rx="4" ry="2.5" fill="#10B981"/>'
                    + f'<ellipse cx="{cx+4}" cy="{GROUND_Y-10}" rx="4" ry="2.5" fill="#047857"/>'
                )
                sub = "no growth yet"
            else:
                trunk_w = 4 + lvl  # widens with level
                trunk_h = 10 + lvl * 9          # L1=19 … L6=64
                crown_r = 4 + lvl * 4           # L1=8  … L6=28
                trunk_top = GROUND_Y - trunk_h
                crown_cy = trunk_top - crown_r // 2 + 2
                extras = ""
                if lvl >= 4:
                    extras = (
                        f'<circle cx="{cx-crown_r+2}" cy="{crown_cy-crown_r//2}" r="{crown_r-6}" fill="#10B981" opacity="0.92"/>'
                        f'<circle cx="{cx+crown_r-2}" cy="{crown_cy-crown_r//2}" r="{crown_r-6}" fill="#047857" opacity="0.92"/>'
                    )
                body = (
                    _shadow(cx, 18 + lvl * 5)
                    + f'<rect x="{cx-trunk_w//2}" y="{trunk_top}" width="{trunk_w}" height="{trunk_h}" rx="2" fill="#7C4A28"/>'
                    + f'<circle cx="{cx}" cy="{crown_cy}" r="{crown_r}" fill="{green}"/>'
                    + f'<circle cx="{cx-crown_r+4}" cy="{crown_cy+crown_r//2}" r="{max(3, crown_r-4)}" fill="#10B981"/>'
                    + f'<circle cx="{cx+crown_r-4}" cy="{crown_cy+crown_r//2}" r="{max(3, crown_r-4)}" fill="#047857"/>'
                    + extras
                )
                sub = ("sapling" if lvl == 1 else
                       "small tree" if lvl == 2 else
                       "young tree" if lvl == 3 else
                       "mature tree" if lvl == 4 else
                       "tall tree" if lvl == 5 else
                       "ancient tree")
            label = _label(cx, name, lvl, sub)
        elif kind == "well":
            # Classic stone wishing well. Stone rim + water surface always
            # visible at L1+. Posts → roof → bucket → glow grow in by level.
            stone_dark = "#7A6E5C"
            stone_mid = "#A89A85"
            stone_light = "#C6B89E"
            wood = "#6B4423"
            roof_col = "#8B4513"
            water_mid = "#3B82F6"
            water_hl = "#93C5FD"

            rim_cy = GROUND_Y - 8
            rim_rx = 22
            rim_ry = 7
            wall_h = 14

            if lvl <= 0:
                # Dry pit. Dark hole in the ground, no walls.
                body = (
                    _shadow(cx, 28)
                    + f'<ellipse cx="{cx}" cy="{GROUND_Y-6}" rx="22" ry="7" fill="#2A2218"/>'
                    + f'<ellipse cx="{cx}" cy="{GROUND_Y-8}" rx="20" ry="5" fill="#1A1410"/>'
                )
                sub = "dry pit"
            else:
                wall_top_y = rim_cy
                wall_bot_y = rim_cy + wall_h
                wall = (
                    f'<path d="M {cx-rim_rx} {wall_top_y} '
                    f'A {rim_rx} {rim_ry} 0 0 0 {cx+rim_rx} {wall_top_y} '
                    f'L {cx+rim_rx} {wall_bot_y} '
                    f'A {rim_rx} {rim_ry-1} 0 0 1 {cx-rim_rx} {wall_bot_y} Z" '
                    f'fill="{stone_mid}"/>'
                )
                brick_lines = (
                    f'<line x1="{cx-rim_rx+3}" y1="{wall_top_y+5}" x2="{cx-2}" y2="{wall_top_y+6}" stroke="{stone_dark}" stroke-width="1" opacity="0.55"/>'
                    f'<line x1="{cx+3}" y1="{wall_top_y+6}" x2="{cx+rim_rx-3}" y2="{wall_top_y+5}" stroke="{stone_dark}" stroke-width="1" opacity="0.55"/>'
                    f'<line x1="{cx-2}" y1="{wall_top_y+6}" x2="{cx-2}" y2="{wall_bot_y-2}" stroke="{stone_dark}" stroke-width="1" opacity="0.5"/>'
                    f'<line x1="{cx-rim_rx+6}" y1="{wall_top_y+10}" x2="{cx-rim_rx+10}" y2="{wall_bot_y-1}" stroke="{stone_dark}" stroke-width="1" opacity="0.5"/>'
                    f'<line x1="{cx+rim_rx-10}" y1="{wall_top_y+10}" x2="{cx+rim_rx-6}" y2="{wall_bot_y-1}" stroke="{stone_dark}" stroke-width="1" opacity="0.5"/>'
                )
                rim = (
                    f'<ellipse cx="{cx}" cy="{rim_cy}" rx="{rim_rx}" ry="{rim_ry}" '
                    f'fill="{stone_light}" stroke="{stone_dark}" stroke-width="1"/>'
                )
                mouth = (
                    f'<ellipse cx="{cx}" cy="{rim_cy+1}" rx="{rim_rx-4}" ry="{rim_ry-2}" '
                    f'fill="#2A2218"/>'
                )
                water_ry = max(1, min(rim_ry - 2, 1 + lvl))
                water = (
                    f'<ellipse cx="{cx}" cy="{rim_cy+2}" rx="{rim_rx-5}" ry="{water_ry}" '
                    f'fill="{water_mid}"/>'
                    f'<ellipse cx="{cx}" cy="{rim_cy+1}" rx="{rim_rx-7}" ry="{max(0.5, water_ry-1.5)}" '
                    f'fill="{water_hl}" opacity="0.7"/>'
                ) if lvl >= 1 else ""

                post_top = rim_cy - 30
                post_l_x = cx - rim_rx + 2
                post_r_x = cx + rim_rx - 2
                posts = ""
                roof = ""
                bucket = ""
                glow = ""
                sparkle = ""
                if lvl >= 2:
                    posts = (
                        f'<rect x="{post_l_x-2}" y="{post_top}" width="4" height="{rim_cy-post_top}" fill="{wood}"/>'
                        f'<rect x="{post_r_x-2}" y="{post_top}" width="4" height="{rim_cy-post_top}" fill="{wood}"/>'
                        f'<rect x="{post_l_x-3}" y="{post_top-3}" width="{(post_r_x-post_l_x)+6}" height="4" fill="{wood}"/>'
                    )
                if lvl >= 3:
                    roof_h = 8 + min(lvl, 6) * 2
                    roof_apex_y = post_top - roof_h - 2
                    roof_overhang = 6
                    roof = (
                        f'<polygon points="'
                        f'{post_l_x-roof_overhang},{post_top-2} '
                        f'{post_r_x+roof_overhang},{post_top-2} '
                        f'{cx},{roof_apex_y}" '
                        f'fill="{roof_col}"/>'
                        f'<polygon points="'
                        f'{post_l_x-roof_overhang},{post_top-2} '
                        f'{cx},{roof_apex_y} '
                        f'{cx-2},{post_top-2}" '
                        f'fill="#A0522D" opacity="0.55"/>'
                    )
                if lvl >= 4:
                    bucket_y = rim_cy - 12
                    bucket = (
                        f'<line x1="{cx}" y1="{post_top-1}" x2="{cx}" y2="{bucket_y}" stroke="#3A2A1A" stroke-width="1"/>'
                        f'<path d="M {cx-5} {bucket_y} L {cx+5} {bucket_y} L {cx+4} {bucket_y+7} L {cx-4} {bucket_y+7} Z" fill="#5C3A1E"/>'
                        f'<rect x="{cx-5}" y="{bucket_y}" width="10" height="2" fill="#3A2A1A"/>'
                    )
                if lvl >= 5:
                    glow = (
                        f'<ellipse cx="{cx}" cy="{rim_cy+1}" rx="{rim_rx+4}" ry="{rim_ry+2}" '
                        f'fill="#FCD34D" opacity="0.18"/>'
                    )
                if lvl >= 6:
                    sparkle = (
                        f'<circle cx="{cx-8}" cy="{rim_cy-2}" r="1.2" fill="#FEF3C7"/>'
                        f'<circle cx="{cx+6}" cy="{rim_cy}" r="1.5" fill="#FEF3C7"/>'
                    )

                body = (
                    _shadow(cx, 32)
                    + wall + brick_lines + rim + mouth
                    + glow + water + sparkle
                    + posts + roof + bucket
                )
                sub = ("damp stone" if lvl == 1 else
                       "shallow water" if lvl == 2 else
                       "half-full" if lvl == 3 else
                       "drawing water" if lvl == 4 else
                       "deep well" if lvl == 5 else
                       "brimming well")
            label = _label(cx, name, lvl, sub)
        elif kind == "thorns":
            # MORE thorns = LOWER level (track of friction). High level = cleared.
            thorns_n = max(1, 7 - a)
            bits = []
            spacing = 8
            start = cx - (thorns_n - 1) * spacing // 2
            base_y = GROUND_Y - 2
            tip_y = base_y - 28
            for k in range(thorns_n):
                px = start + k * spacing
                bits.append(f'<path d="M {px-3} {base_y} L {px} {tip_y} L {px+3} {base_y} Z" fill="{red}" opacity="0.9"/>')
            body = (
                _shadow(cx, 40)
                + "".join(bits)
                + f'<path d="M {cx-30} {GROUND_Y+2} C {cx-10} {GROUND_Y-14}, {cx+10} {GROUND_Y-14}, {cx+30} {GROUND_Y+2}" '
                f'fill="none" stroke="#7F1D1D" stroke-width="3"/>'
            )
            warn = lvl <= 2
            sub = "needs clearing" if warn else ("thinning" if lvl <= 4 else "cleared")
            label = _label(cx, name, lvl, sub, warn=warn)
        elif kind == "path":
            # stones laid: more stones = higher level
            stones_n = max(2, min(6, 1 + a))
            bits = []
            span = 60
            for k in range(stones_n):
                px = cx - span // 2 + int(k * span / max(1, stones_n - 1))
                py = (GROUND_Y - 8) + ((k % 2) * 2)
                bits.append(f'<ellipse cx="{px}" cy="{py}" rx="6" ry="3" fill="#7C4A28"/>')
            body = _shadow(cx, 44) + "".join(bits)
            sub = "stepping stones" if lvl <= 3 else "laid path"
            label = _label(cx, name, lvl, sub)
        elif kind == "shrine":
            if lvl <= 0:
                # bare plot: just a stone slab on the ground.
                body = (
                    _shadow(cx, 30)
                    + f'<rect x="{cx-22}" y="{GROUND_Y-6}" width="44" height="6" rx="1" fill="#8C7A5C"/>'
                    + f'<rect x="{cx-16}" y="{GROUND_Y-9}" width="4" height="3" fill="#7C6A4E"/>'
                    + f'<rect x="{cx+10}" y="{GROUND_Y-9}" width="4" height="3" fill="#7C6A4E"/>'
                )
                sub = "bare plot"
            else:
                # cols: L1=1 single stake → L6=6 full shrine
                cols = lvl
                col_w = 7
                inner_span = min(54, 8 + cols * 8)
                col_h = 22 + lvl * 2
                base_y = GROUND_Y - 6
                col_top = base_y - col_h
                roof_y = col_top - 2
                apex_y = roof_y - (10 + min(lvl, 4) * 2)
                star_y = apex_y - 8
                col_xs = (
                    [cx - inner_span // 2 + int(k * inner_span / (cols - 1)) for k in range(cols)]
                    if cols > 1 else [cx]
                )
                col_bits = "".join(
                    f'<rect x="{x - col_w//2}" y="{col_top}" width="{col_w}" height="{col_h}" fill="{purple}"/>'
                    for x in col_xs
                )
                base_w = max(18, inner_span + 14)
                roof_w = max(20, inner_span + 18)
                roof_extra = ""
                if lvl >= 1:
                    roof_extra = (
                        f'<rect x="{cx-base_w//2}" y="{base_y}" width="{base_w}" height="6" fill="{purple}"/>'
                        + f'<polygon points="{cx-roof_w//2},{roof_y} {cx+roof_w//2},{roof_y} {cx},{apex_y}" fill="{purple}"/>'
                    )
                capstone = (
                    f'<circle cx="{cx}" cy="{star_y}" r="{2+min(lvl, 5)}" fill="#FCD34D"/>'
                    if lvl >= 2 else ""
                )
                body = (
                    _shadow(cx, 30 + lvl * 3)
                    + roof_extra
                    + col_bits
                    + capstone
                )
                sub = ("single stake" if lvl == 1 else
                       "two pillars" if lvl == 2 else
                       "colonnade" if lvl == 3 else
                       "small shrine" if lvl == 4 else
                       "full shrine" if lvl == 5 else
                       "golden shrine")
            label = _label(cx, name, lvl, sub)
        else:  # post / signpost
            if lvl <= 0:
                body = (
                    _shadow(cx, 12)
                    + f'<rect x="{cx-2}" y="{GROUND_Y-14}" width="4" height="14" rx="1" fill="#7C4A28"/>'
                )
                sub = "bare stake"
            else:
                arms = max(1, min(6, lvl))
                post_h = 28 + arms * 5     # L1=33 … L6=58
                post_top = GROUND_Y - post_h
                arm_gap = 8
                arm_w = 8
                arm_zone_h = (arms - 1) * arm_gap + arm_w
                arm_top = post_top + max(0, (post_h - arm_zone_h - 4) // 2) + 2
                bits = []
                # widest arm at top, tapering as we go down (signpost silhouette)
                widths = [30, 28, 26, 24, 22, 20][:arms]
                for k in range(arms):
                    ay = arm_top + k * arm_gap
                    w = widths[k]
                    if k % 2 == 0:
                        bits.append(
                            f'<path d="M {cx-w} {ay} H {cx+w-4} L {cx+w-12} {ay+arm_w} H {cx-w} Z" '
                            f'fill="{amber}"/>'
                        )
                    else:
                        bits.append(
                            f'<path d="M {cx+w} {ay} H {cx-w+4} L {cx-w+12} {ay+arm_w} H {cx+w} Z" '
                            f'fill="#F59E0B"/>'
                        )
                body = (
                    _shadow(cx, 20)
                    + f'<rect x="{cx-4}" y="{post_top}" width="8" height="{post_h}" rx="2" fill="#7C4A28"/>'
                    + "".join(bits)
                )
                sub = ("lone post" if lvl == 1 else
                       "one direction" if lvl == 2 else
                       "signposted" if lvl == 3 else
                       "well-marked" if lvl == 4 else
                       "crossroads" if lvl == 5 else
                       "waymarker hub")
            label = _label(cx, name, lvl, sub)

        glyphs.append(f'<g><title>{name} — level {lvl}</title>{body}{label}</g>')

    # Stars: one per track that hit L>=2, fade-in by count. Full constellation only when balanced.
    star_positions = [
        (110, 60), (220, 42), (340, 70), (460, 38),
        (560, 62), (640, 44), (740, 70), (160, 96), (620, 100),
    ]
    star_bits = []
    star_count = max(0, tracks_lit)
    # Day-mode = small amber sparkles (visible against cream sky).
    # Night-mode = soft pale-yellow stars + 4-point glints.
    for k, (sx, sy) in enumerate(star_positions[:star_count]):
        r = 2.0 + (k % 3) * 0.6
        if balanced:
            star_bits.append(
                f'<circle cx="{sx}" cy="{sy}" r="{r:.1f}" fill="#FDE68A" opacity="0.95"/>'
                f'<path d="M {sx} {sy-r-2} L {sx+1} {sy} L {sx} {sy+r+2} L {sx-1} {sy} Z" '
                f'fill="#FFFFFF" opacity="0.85"/>'
                f'<path d="M {sx-r-2} {sy} L {sx} {sy+1} L {sx+r+2} {sy} L {sx} {sy-1} Z" '
                f'fill="#FFFFFF" opacity="0.85"/>'
            )
        else:
            # 4-point amber spark — readable on the day sky.
            star_bits.append(
                f'<g opacity="0.9"><circle cx="{sx}" cy="{sy}" r="{r-0.4:.1f}" fill="#F59E0B"/>'
                f'<path d="M {sx} {sy-r-3} L {sx+1.2} {sy} L {sx} {sy+r+3} L {sx-1.2} {sy} Z" '
                f'fill="#F59E0B"/>'
                f'<path d="M {sx-r-3} {sy} L {sx} {sy+1.2} L {sx+r+3} {sy} L {sx} {sy-1.2} Z" '
                f'fill="#F59E0B"/></g>'
            )

    # Sky: day gradient unless balanced (then evening). Soft clouds fade out at night.
    sun_x, sun_y = 770, 56
    # Suffix gradient IDs by palette so multiple grove SVGs on one page
    # (e.g. the level-showcase) don't collide on the global ID namespace.
    palette_id = "night" if balanced else "day"
    sky_id = f"grove_sky_{palette_id}"
    ground_id = f"grove_ground_{palette_id}"
    if balanced:
        # Real night. Sky AND ground darken so contrast reads as evening,
        # not "blue tint over daytime".
        sky_top, sky_bot = "#0B0922", "#231640"
        ground_top, ground_bot = "#2E5C3A", "#1F4029"
        ground_strip = "#173026"
        cloud_op = 0.08
        sun_disc = (
            f'<circle cx="{sun_x}" cy="{sun_y}" r="22" fill="#FEF3C7" opacity="0.30"/>'
            f'<circle cx="{sun_x}" cy="{sun_y}" r="16" fill="#FEF3C7" opacity="0.98"/>'
            f'<circle cx="{sun_x-5}" cy="{sun_y-3}" r="3" fill="#E5D08C" opacity="0.85"/>'
            f'<circle cx="{sun_x+4}" cy="{sun_y+5}" r="2" fill="#E5D08C" opacity="0.8"/>'
            f'<circle cx="{sun_x+2}" cy="{sun_y-6}" r="1.6" fill="#E5D08C" opacity="0.75"/>'
        )
    else:
        sky_top, sky_bot = "#FDECC8", "#FFF8E7"
        ground_top, ground_bot = "#D9F0C3", "#A7D58D"
        ground_strip = "#7FB269"
        cloud_op = 0.7
        sun_disc = (
            f'<circle cx="{sun_x}" cy="{sun_y}" r="26" fill="#FCD34D" opacity="0.18"/>'
            f'<circle cx="{sun_x}" cy="{sun_y}" r="18" fill="#FCD34D" opacity="0.95"/>'
        )

    title_line = "Daimon Grove · The Plot"
    sub_line = (
        "Every site at Level 2 or higher — night sky lit." if balanced
        else f"{tracks_lit} of 6 sites at L2+. Sky turns to night when all six clear L2."
    )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMinYMin meet" '
        f'role="img" aria-label="Daimon Grove village plot">'
        f'<title>Daimon Grove</title>'
        f'<desc>Village-plot view of the Daimon Grove with six craft sites — Command Tree, Memory Well, '
        f'Git Thorns, Planning Path, Tool Shrine, and Repo Signpost — sharing one landscape. No path '
        f'connects them; each grows in parallel as evidence proves improvement. When every site reaches '
        f'Level 2, the sky turns to evening and a constellation appears.</desc>'
        f'<defs>'
        f'<linearGradient id="{sky_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{sky_top}"/>'
        f'<stop offset="100%" stop-color="{sky_bot}"/>'
        f'</linearGradient>'
        f'<linearGradient id="{ground_id}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{ground_top}"/>'
        f'<stop offset="100%" stop-color="{ground_bot}"/>'
        f'</linearGradient>'
        f'</defs>'
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="14" fill="url(#{sky_id})"/>'
        # rolling hills, no road
        f'<path d="M 0 200 C 120 168, 230 222, 360 198 S 600 174, 780 206 S 900 188, 900 216 V {height} H 0 Z" fill="url(#{ground_id})"/>'
        f'<path d="M 0 224 C 200 208, 380 246, 540 224 S 800 232, 900 244 V {height} H 0 Z" fill="{ground_strip}" opacity="0.6"/>'
        # clouds (fade out at night)
        f'<g fill="#FFFFFF" opacity="{cloud_op}">'
        f'<ellipse cx="140" cy="52" rx="32" ry="9"/>'
        f'<ellipse cx="170" cy="48" rx="20" ry="7"/>'
        f'<ellipse cx="560" cy="44" rx="36" ry="10"/>'
        f'<ellipse cx="600" cy="38" rx="20" ry="7"/>'
        f'</g>'
        + sun_disc
        + "".join(star_bits)
        + f'<text x="22" y="26" font-size="14" font-weight="800" fill="{ink}" font-family="-apple-system,sans-serif">{title_line}</text>'
        f'<text x="22" y="42" font-size="11" fill="{muted}" font-family="-apple-system,sans-serif">{sub_line}</text>'
        + "".join(glyphs)
        + '</svg>'
    )


__all__ = [
    "GAME_SCHEMA_VERSION", "TRACKS", "QUEST_DEFS", "BADGE_ORDER",
    "display_track_name", "format_delta", "safe_rate_text",
    "build_grove_summary", "choose_active_quest", "badge_unlocks_from_evidence",
    "build_game_state", "numeric_game_history_snapshot", "render_grove_svg",
]
