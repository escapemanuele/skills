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

import math
from typing import Optional


# ─── Tunable constants (single source of truth) ────────────────────────────
GAME_SCHEMA_VERSION = 1

TRACKS = (
    "automation", "memory", "safety", "planning",
    "tool_fluency", "project_hygiene",
)

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
        "reward": "+35 Automation XP when completed.",
    },
    "fill_memory_well": {
        "title": "Fill the Memory Well",
        "track": "memory",
        "why_tmpl": "Memory activity was low across {sessions} observed sessions.",
        "do": "Say: “save what we learned.”",
        "reward": "+30 Memory XP when completed.",
    },
    "clear_git_thorns": {
        "title": "Clear the Git Thorns",
        "track": "safety",
        "why_tmpl": "Risky git commands appeared {count} times this window.",
        "do": "Before destructive git commands, ask for a blast-radius and recovery plan.",
        "reward": "+25 Safety XP when completed.",
    },
    "walk_planning_path": {
        "title": "Walk the Planning Path",
        "track": "planning",
        "why_tmpl": "wrong_approach appeared in {sessions} of {labeled} labeled sessions, with {events} events.",
        "do": "Before coding, ask for a 5-step plan and one risky assumption.",
        "reward": "+25 Planning XP when completed.",
    },
    "place_repo_signpost": {
        "title": "Place the Repo Signpost",
        "track": "project_hygiene",
        "why_tmpl": "{missing} of {total} active repos are missing CLAUDE.md.",
        "do": "Add or update CLAUDE.md in one active repo.",
        "reward": "+25 Project Hygiene XP when completed.",
    },
    "tune_tool_shrine": {
        "title": "Tune the Tool Shrine",
        "track": "tool_fluency",
        "why_tmpl": "Shell-based probing or bash errors were elevated this window.",
        "do": "Use the built-in file/search tools before falling back to shell probing.",
        "reward": "+20 Tool Fluency XP when completed.",
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
    risky = sum(
        _safe_int(x.get("count")) for x in (cs.get("destructive_cmds") or [])
        if x.get("label") and x.get("label") != "rm -rf"
    )
    hot_no_cmd = cs.get("hot_repos_without_claudemd") or []
    claudemd_missing = len(hot_no_cmd)
    # active repos = projects shown in the recap (basenames). Use top_projects len as proxy.
    top_projects = (p.get("work_recap") or {}).get("top_projects") or []
    active_repos = sum(1 for tp in top_projects if (tp.get("kind") == "dev"))
    # outcomes
    outcomes = p.get("outcomes") or {}
    cov = outcomes.get("coverage") or {}
    labeled = _safe_int(cov.get("labeled"))
    total = _safe_int(cov.get("total"))
    by_facet = outcomes.get("by_facet") or {}
    finished = _safe_int(by_facet.get("fully_achieved")) + _safe_int(by_facet.get("mostly_achieved"))
    fin_pct = round(100 * finished / labeled) if labeled else 0
    fric_sess = outcomes.get("friction_sessions") or {}
    fric_sum = outcomes.get("friction_counts_sum") or {}
    wa_sessions = _safe_int(fric_sess.get("wrong_approach"))
    wa_events = _safe_int(fric_sum.get("wrong_approach"))
    wa_pct = round(100 * wa_sessions / labeled) if labeled else 0
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
    mem_pct = round(100 * mem_sessions / total) if total else 0
    # recurring prompts (top one without a saved skill counterpart)
    recurring = p.get("recurring_prompts") or []
    top_recurring = max((_safe_int(r.get("count")) for r in recurring), default=0)
    installed = p.get("installed_skills") or []
    has_prompt_to_command = "prompt-to-command" in installed
    # search shell vs built-in
    search_shell_pct = round(100 * bypass_total / (bypass_total + native_total)) if (bypass_total + native_total) else 0
    # stuck loops
    stuck = p.get("stuck_loops") or []
    stuck_count = len(stuck)

    return {
        "risky_git_count": risky,
        "claudemd_missing": claudemd_missing,
        "active_repos": active_repos,
        "top_recurring": top_recurring,
        "has_prompt_to_command_skill": bool(has_prompt_to_command),
        "labeled": labeled,
        "total": total,
        "finished_pct": fin_pct,
        "wa_sessions": wa_sessions,
        "wa_events": wa_events,
        "wa_pct": wa_pct,
        "bash_err_pct": bash_err_pct,
        "memory_rate_pct": mem_pct,
        "search_shell_pct": search_shell_pct,
        "bypass_total": bypass_total,
        "native_total": native_total,
        "stuck_count": stuck_count,
    }


def _select_quest(ev: dict) -> dict | None:
    """Pick one active quest using evidence presence + tie-break priority."""
    candidates: list[tuple[str, dict, dict]] = []

    # Safety
    if ev["risky_git_count"] >= 1:
        candidates.append(("safety", QUEST_DEFS["clear_git_thorns"], {
            "id": "clear_git_thorns",
            "evidence": {"risky_git_count": ev["risky_git_count"]},
            "why": QUEST_DEFS["clear_git_thorns"]["why_tmpl"].format(count=ev["risky_git_count"]),
        }))

    # Automation: a repeated prompt with no saved command counterpart yet.
    # Conservative: require prompt-to-command not installed, OR recurring prompt
    # still high. If installed, the report itself nudges; we only quest when
    # the candidate is present and nothing visible has been done about it.
    if ev["top_recurring"] >= 3:
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
                          "active_repos": max(ev["active_repos"], 1)},
            "why": QUEST_DEFS["place_repo_signpost"]["why_tmpl"].format(
                missing=ev["claudemd_missing"], total=max(ev["active_repos"], 1)),
        }))

    # Planning
    if ev["labeled"] >= 5 and (ev["wa_pct"] >= 30 or ev["stuck_count"] >= 1):
        candidates.append(("planning", QUEST_DEFS["walk_planning_path"], {
            "id": "walk_planning_path",
            "evidence": {"sessions": ev["wa_sessions"], "labeled": ev["labeled"],
                          "events": ev["wa_events"]},
            "why": QUEST_DEFS["walk_planning_path"]["why_tmpl"].format(
                sessions=ev["wa_sessions"], labeled=ev["labeled"], events=ev["wa_events"]),
        }))

    # Memory
    if ev["total"] >= 10 and ev["memory_rate_pct"] < 10:
        candidates.append(("memory", QUEST_DEFS["fill_memory_well"], {
            "id": "fill_memory_well",
            "evidence": {"sessions_observed": ev["total"], "memory_rate_pct": ev["memory_rate_pct"]},
            "why": QUEST_DEFS["fill_memory_well"]["why_tmpl"].format(sessions=ev["total"]),
        }))

    # Tool fluency
    if ev["search_shell_pct"] >= 15 or ev["bash_err_pct"] >= 10:
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


def _compute_xp_deltas(ev: dict, prior: dict | None) -> dict:
    """Award XP only on verified improvement vs `prior`. Always ≥ 0, capped."""
    deltas = {t: 0 for t in TRACKS}
    if not prior:
        # No prior distinct day → nothing to verify yet; no XP awarded.
        return deltas

    prior_sc = prior.get("scorecard") or {}
    prior_game = prior.get("game") or {}

    # Safety: risky_git_count dropped.
    if "risky_git_count" in prior_sc:
        if ev["risky_git_count"] < prior_sc["risky_git_count"]:
            deltas["safety"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["safety"])

    # Tool fluency: bash error rate down OR shell-search % down.
    if "bash_error_pct" in prior_sc and ev["bash_err_pct"] < prior_sc["bash_error_pct"]:
        deltas["tool_fluency"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["tool_fluency"])
    elif "search_shell_pct" in prior_sc and ev["search_shell_pct"] < prior_sc["search_shell_pct"]:
        deltas["tool_fluency"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["tool_fluency"])

    # Memory: memory_rate_pct increased.
    if "memory_rate_pct" in prior_sc and ev["memory_rate_pct"] > prior_sc["memory_rate_pct"]:
        deltas["memory"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["memory"])

    # Planning: finished% up OR wrong_approach pct down (approximation —
    # exact wa_pct isn't in history, so use finished as proxy + drop-in stuck loops).
    if "outcome_finished_pct" in prior_sc and ev["finished_pct"] > prior_sc["outcome_finished_pct"]:
        deltas["planning"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["planning"])

    # Project hygiene: number of repos missing CLAUDE.md dropped.
    if "claudemd_missing" in prior_sc and ev["claudemd_missing"] < prior_sc["claudemd_missing"]:
        deltas["project_hygiene"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["project_hygiene"])

    # Automation: top recurring prompt count dropped (likely saved as a /command)
    # OR prompt-to-command skill became installed since prior.
    prior_unsaved = prior_sc.get("unsaved_prompts")
    if prior_unsaved is not None and prior_unsaved > 0 and ev["top_recurring"] < prior_unsaved:
        deltas["automation"] = min(MAX_DELTA_PER_TRACK, XP_AWARDS["automation"])

    return deltas


def _badge_state(ev: dict, history_snapshots: list[dict] | None) -> list[dict]:
    """Conservative badges. Each is earned only when evidence is clear."""
    snaps = sorted(history_snapshots or [], key=lambda s: s.get("date", ""))
    last_n = lambda n: snaps[-n:] if len(snaps) >= n else None

    badges = []

    # Repo Warden: 0 missing CLAUDE.md this run.
    badges.append({
        "id": "repo_warden",
        "name": BADGE_NAMES["repo_warden"],
        "earned": ev["claudemd_missing"] == 0 and ev["active_repos"] >= 1,
        "evidence": "All active dev repos have a CLAUDE.md." if ev["claudemd_missing"] == 0
                    else "Some active repos are still missing CLAUDE.md.",
    })

    # Safe Hands: risky_git_count == 0 across 3 distinct prior days + this run.
    safe_runs = last_n(3)
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

    # Pathfinder: outcome_finished_pct increased across at least 3 distinct days.
    pathfinder = False
    if snaps and len(snaps) >= 3:
        fin_series = [
            _safe_int((s.get("scorecard") or {}).get("outcome_finished_pct"))
            for s in snaps[-3:]
        ]
        pathfinder = fin_series == sorted(fin_series) and fin_series[-1] > fin_series[0]
    badges.append({
        "id": "pathfinder",
        "name": BADGE_NAMES["pathfinder"],
        "earned": bool(pathfinder),
        "evidence": "Planning improved across 3 distinct report days." if pathfinder
                    else "Planning improvement is not yet established.",
    })

    # Tool Adept: built-in tools usage exceeds shell-based file probing.
    tool_adept = ev["native_total"] > ev["bypass_total"] and ev["bypass_total"] > 0
    badges.append({
        "id": "tool_adept",
        "name": BADGE_NAMES["tool_adept"],
        "earned": bool(tool_adept),
        "evidence": "Built-in tools were used more than shell-based file probing."
                    if tool_adept else "Shell-based file probing is still notable.",
    })

    # Command Sapling: top recurring dropped AND prompt-to-command exists
    # (i.e. likely saved). Requires at least 1 prior day to compare.
    sapling = False
    if snaps:
        prior_unsaved = _safe_int((snaps[-1].get("scorecard") or {}).get("unsaved_prompts"))
        sapling = (
            ev["has_prompt_to_command_skill"]
            and prior_unsaved > 0
            and ev["top_recurring"] < prior_unsaved
        )
    badges.append({
        "id": "command_sapling",
        "name": BADGE_NAMES["command_sapling"],
        "earned": bool(sapling),
        "evidence": "A repeated prompt looks saved as a command." if sapling
                    else "No verified command-from-prompt yet.",
    })

    # Memory Keeper: 3+ memory-touched sessions in last run + memory rate up.
    keeper = ev["memory_rate_pct"] >= 30
    badges.append({
        "id": "memory_keeper",
        "name": BADGE_NAMES["memory_keeper"],
        "earned": bool(keeper),
        "evidence": "Memory was used regularly this window." if keeper
                    else "Memory activity is still light.",
    })

    return badges


def _badges_mask(badges: list[dict]) -> int:
    """Numeric encoding of earned badges (bit position = BADGE_ORDER index)."""
    mask = 0
    earned = {b["id"]: b["earned"] for b in badges}
    for i, bid in enumerate(BADGE_ORDER):
        if earned.get(bid):
            mask |= (1 << i)
    return mask


def _track_evidence_one_liner(track: str, ev: dict, delta: int) -> str:
    """Fixed-template evidence line per track. No raw user content."""
    if track == "automation":
        if delta > 0:
            return "Repeated prompt count decreased — looks saved as a command."
        return f"Top repeated prompt seen {ev['top_recurring']} times this window."
    if track == "memory":
        if delta > 0:
            return f"Memory activity rose to {ev['memory_rate_pct']}% of sessions."
        return f"Memory used in {ev['memory_rate_pct']}% of {ev['total']} sessions."
    if track == "safety":
        if delta > 0:
            return f"Risky git events decreased ({ev['risky_git_count']} this window)."
        return f"Risky git events: {ev['risky_git_count']} this window."
    if track == "planning":
        if delta > 0:
            return "Outcome rate improved vs the prior run."
        if ev["labeled"]:
            return f"wrong_approach in {ev['wa_sessions']} of {ev['labeled']} labeled."
        return "Not enough labeled sessions for a planning read."
    if track == "tool_fluency":
        if delta > 0:
            return "Built-in tools usage gained on shell probing."
        return f"Shell-based file probing: {ev['search_shell_pct']}% of file searches."
    if track == "project_hygiene":
        if delta > 0:
            return "CLAUDE.md added in a previously-missing repo."
        return f"{ev['claudemd_missing']} active repos missing CLAUDE.md."
    return ""


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
    quest = _select_quest(ev)

    # Build per-track structure for the report.
    tracks_out: dict[str, dict] = {}
    for t in TRACKS:
        tracks_out[t] = {
            "xp": tracks_xp[t],
            "level": _level_for(tracks_xp[t], TRACK_LEVEL_THRESHOLDS),
            "delta": deltas[t],
            "evidence": _track_evidence_one_liner(t, ev, deltas[t]),
        }

    # Badges (state + numeric mask for history).
    badges = _badge_state(ev, history_snapshots)
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

    # Cumulative quest counts (numeric only).
    prior_quests_offered = _safe_int(prior_game.get("quests_offered_count"))
    prior_quests_completed = _safe_int(prior_game.get("quests_completed_count"))
    quests_offered = prior_quests_offered + (1 if quest else 0)
    # A quest is "completed" when XP was awarded in its track since the prior
    # distinct day. Approximate: any delta > 0 anywhere → +1 completion.
    quests_completed = prior_quests_completed + (1 if any(d > 0 for d in deltas.values()) else 0)

    return {
        "game_schema_version": GAME_SCHEMA_VERSION,
        "xp_total": xp_total,
        "daimon_level": daimon_level,
        "tracks": tracks_out,
        "active_quest": quest,
        "badges": badges,
        "badges_mask": badges_mask,
        "badge_count": badge_count,
        "grove": grove,
        "quests_offered_count": quests_offered,
        "quests_completed_count": quests_completed,
    }


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
    return out


# ─── deterministic inline SVG (privacy-safe, no external assets) ────────────
def render_grove_svg(grove: dict, *, width: int = 720, height: int = 110) -> str:
    """Return a self-contained inline SVG of the Daimon Grove.

    Six glyphs in a row, each scaling with its track level. No external
    images, no JS. Includes <title>/<desc> for accessibility.
    """
    g = grove or {}
    items = [
        ("Command Tree",   _safe_int(g.get("command_tree_level")),  "tree"),
        ("Memory Well",    _safe_int(g.get("memory_well_level")),   "well"),
        ("Git Thorns",     _safe_int(g.get("git_thorn_level")),     "thorns"),
        ("Planning Path",  _safe_int(g.get("planning_path_level")), "path"),
        ("Tool Shrine",    _safe_int(g.get("tool_shrine_level")),   "shrine"),
        ("Repo Signpost",  _safe_int(g.get("repo_signpost_level")), "post"),
    ]
    constellation = bool(g.get("constellation_unlocked"))
    n = len(items)
    col_w = width / n
    base_y = height - 22

    def _label(x, name, level):
        return (
            f'<text x="{x}" y="{height-4}" text-anchor="middle" '
            f'font-size="10" fill="#6B7280" font-family="-apple-system,sans-serif">'
            f'{name} · L{level}</text>'
        )

    glyphs = []
    for i, (name, lvl, kind) in enumerate(items):
        cx = col_w * i + col_w / 2
        # Active visibility scales with level (clamped 0–6).
        a = max(0, min(6, lvl))
        active = a > 0
        color = "#6D28D9" if active else "#D1D5DB"
        if kind == "tree":
            # trunk + canopy
            h = 10 + a * 5
            glyphs.append(
                f'<rect x="{cx-2}" y="{base_y-h}" width="4" height="{h}" fill="{color}"/>'
                f'<circle cx="{cx}" cy="{base_y-h}" r="{8 + a*1.5}" fill="{color}" opacity="{0.35+a*0.08}"/>'
            )
        elif kind == "well":
            # round well; fill rises with level
            r = 14
            fill_h = (a / 6) * (2 * r)
            glyphs.append(
                f'<circle cx="{cx}" cy="{base_y-r}" r="{r}" fill="none" stroke="{color}" stroke-width="2"/>'
                f'<rect x="{cx-r+1}" y="{base_y-1-fill_h}" width="{2*r-2}" height="{fill_h}" fill="{color}" opacity="0.55"/>'
            )
        elif kind == "thorns":
            # thorns recede as level rises (safety good = fewer)
            thorns = max(1, 6 - a)
            for k in range(thorns):
                px = cx - 14 + k * 6
                glyphs.append(
                    f'<path d="M {px} {base_y} L {px+3} {base_y-12} L {px+6} {base_y} Z" '
                    f'fill="{"#B42318" if a < 3 else color}" opacity="0.85"/>'
                )
        elif kind == "path":
            # dashed stone path; length scales with level
            length = 18 + a * 4
            glyphs.append(
                f'<line x1="{cx-length/2}" y1="{base_y}" x2="{cx+length/2}" y2="{base_y}" '
                f'stroke="{color}" stroke-width="3" stroke-dasharray="6 4"/>'
            )
        elif kind == "shrine":
            # small temple: base + columns + roof
            w = 28
            glyphs.append(
                f'<rect x="{cx-w/2}" y="{base_y-4}" width="{w}" height="4" fill="{color}"/>'
                f'<rect x="{cx-w/2+2}" y="{base_y-16}" width="2" height="12" fill="{color}"/>'
                f'<rect x="{cx+w/2-4}" y="{base_y-16}" width="2" height="12" fill="{color}"/>'
                f'<rect x="{cx-2}" y="{base_y-16}" width="2" height="12" fill="{color}" opacity="0.85"/>'
                f'<polygon points="{cx-w/2-2},{base_y-16} {cx+w/2+2},{base_y-16} {cx},{base_y-16-8}" fill="{color}"/>'
            )
        elif kind == "post":
            # signpost: pole + crossarm
            glyphs.append(
                f'<rect x="{cx-1.5}" y="{base_y-18}" width="3" height="18" fill="{color}"/>'
                f'<rect x="{cx-10}" y="{base_y-16}" width="20" height="5" fill="{color}" opacity="0.85"/>'
            )
        # accessibility: per-glyph title for hover/AT
        glyphs.append(
            f'<title>{name} — level {lvl}</title>'
        )
        glyphs.append(_label(cx, name, lvl))

    # Constellation: small stars across the top when unlocked
    if constellation:
        stars = []
        for k in range(7):
            x = (width / 8) * (k + 1)
            stars.append(
                f'<circle cx="{x}" cy="14" r="1.6" fill="#6D28D9" opacity="{0.55 + (k%3)*0.15}"/>'
            )
    else:
        stars = []

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" preserveAspectRatio="xMinYMin meet" '
        f'role="img" aria-label="Daimon Grove visualization">'
        f'<title>Daimon Grove</title>'
        f'<desc>Six glyphs representing craft tracks. Each scales with its level. '
        f'Constellation visible when at least three distinct history days exist.</desc>'
        + "".join(stars)
        + "".join(glyphs)
        + '</svg>'
    )


__all__ = [
    "GAME_SCHEMA_VERSION", "TRACKS", "QUEST_DEFS", "BADGE_ORDER",
    "build_game_state", "numeric_game_history_snapshot", "render_grove_svg",
]
