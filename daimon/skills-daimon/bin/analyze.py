#!/usr/bin/env python3
"""
skills-daimon deterministic analyzer.

Takes scan.py JSON (file arg or stdin) and produces a structured analysis
payload: the verdict, archetype, primary action, scorecard, coaching cards,
history snapshot, render payload, and a markdown report skeleton — all computed
deterministically from hard counts, no LLM.

What this script does NOT do, by design:
  - It never invents a skill/plugin recommendation. Catalog lookup is live and
    belongs to the Claude session (and bin/catalog_search.py). So `recommendations`
    and `gaps` come back EMPTY here; the markdown skeleton carries clearly marked
    placeholders for Claude to fill from real catalog hits. This makes "never
    invent" structural, not a matter of prompt discipline.

Evidence rules enforced here:
  - Every rate carries its denominator.
  - Outcome rates use outcomes.coverage.labeled, never total sessions.
  - Memory rate uses sessions_with_memory / session_count.
  - Risky-git ignores `rm -rf` (normal cleanup), counts force-push / reset --hard
    / --no-verify / clean -fd.

Usage:
    python3 analyze.py scan.json
    python3 analyze.py < scan.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Shared redactor + sibling modules.
sys.path.insert(0, str(Path(__file__).parent))
from redact import redact_in  # noqa: E402

# Placeholder markers Claude replaces with live catalog-backed content.
REC_PLACEHOLDER = "<!-- RECOMMENDATIONS: fill from live catalog query (Step 3). Never invent. -->"
GAP_PLACEHOLDER = "<!-- GAPS: list jobs with no catalog match as 'Worth building yourself'. -->"

# Risky-git labels (must match scan.DESTRUCTIVE_PATTERNS labels), minus rm -rf.
RISKY_GIT_LABELS = {
    "git push --force",
    "git reset --hard",
    "git clean -fd",
    "--no-verify (skips hooks)",
}

# Friction → suggested behavior fix (raw enum keys).
FRICTION_FIX = {
    "wrong_approach": "Plan before coding. Try 'let's plan this first' on big changes.",
    "buggy_code": "Smaller diffs; ask Claude to add a quick test alongside the change.",
    "misunderstood_request": "Open with one sentence of context, then the ask.",
    "user_rejected_action": "Have Claude show the plan before making changes.",
}


# --------------------------------------------------------------------------
# Small safe accessors
# --------------------------------------------------------------------------
def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _pct(num: int, den: int) -> int | None:
    """Integer percent, or None when the denominator is zero (no fake rates)."""
    if not den:
        return None
    return round(100 * num / den)


# API list prices $/M tokens (output, cache_read) — the two axes that dominate
# session cost. Used only for the API-equivalent model-waste estimate; always
# label the result "API-equivalent". Fable is unpublished → priced at the Opus
# floor (conservative).
MODEL_PRICES = {
    "opus":   (75.0, 1.5),
    "fable":  (75.0, 1.5),
    "sonnet": (15.0, 0.3),
    "haiku":  (5.0, 0.1),
}
PREMIUM_FAMILIES = ("opus", "fable")


def _fmt_tokens(n: int) -> str:
    """Human token count: 850 → '850', 57_400 → '57k', 2_720_000 → '2.7M'."""
    n = _int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def _basename(p) -> str:
    """Last path segment, handling both POSIX (/) and Windows (\\) separators.

    Never let a full filesystem path (which can embed the OS username) reach a
    disk artifact — only the leaf directory name is non-identifying enough."""
    s = str(p or "").replace("\\", "/").rstrip("/")
    return s.split("/")[-1]


# --------------------------------------------------------------------------
# Metrics — every value here keeps its denominator alongside it
# --------------------------------------------------------------------------
def compute_metrics(scan: dict) -> dict:
    session_count = _int(scan.get("session_count"))

    outcomes = scan.get("outcomes") or {}
    coverage = outcomes.get("coverage") or {}
    labeled = _int(coverage.get("labeled"))
    total = _int(coverage.get("total")) or session_count
    by_facet = outcomes.get("by_facet") or {}
    finished = _int(by_facet.get("fully_achieved")) + _int(by_facet.get("mostly_achieved"))
    finished_pct = _pct(finished, labeled)
    coverage_pct = _pct(labeled, total)

    friction = outcomes.get("friction_sessions") or {}
    wrong_approach = _int(friction.get("wrong_approach"))
    wrong_approach_pct = _pct(wrong_approach, labeled)
    top_friction = None
    if friction:
        top_friction = max(friction.items(), key=lambda kv: _int(kv[1]))

    cs = scan.get("coaching_signals") or {}
    bypass = cs.get("native_tool_bypass") or {}
    bash_total = _int(bypass.get("bash_total"))
    bypass_total = _int(bypass.get("bypass_total"))
    bypass_pct = _pct(bypass_total, bash_total)
    native = bypass.get("native_tool_use") or {}
    native_total = sum(_int(native.get(t)) for t in ("Grep", "Glob", "Read"))

    # Measured token waste (chars → tokens ≈ /4). "Saved the daimon way" =
    # what the bypass output actually cost minus what the same lookups would
    # have cost through Grep/Glob/Read (measured avg, conservative fallback).
    bypass_out_tokens = _int(bypass.get("bypass_result_chars")) // 4
    bypass_measured = _int(bypass.get("bypass_results_measured"))
    native_out_tokens = _int(bypass.get("native_result_chars")) // 4
    native_measured = _int(bypass.get("native_results_measured"))
    if native_measured >= 10:
        native_avg_tokens = native_out_tokens / native_measured
    else:
        native_avg_tokens = 300  # conservative default when unmeasured
    bypass_native_est_tokens = round(bypass_measured * native_avg_tokens)
    bypass_saved_tokens = max(0, bypass_out_tokens - bypass_native_est_tokens)
    error_waste_tokens = _int(cs.get("bash_error_chars")) // 4
    window_tokens = sum(
        _int(p.get("tokens"))
        for p in (scan.get("work_recap") or {}).get("top_projects") or []
    )

    # Model mix: share of output on premium families + automation-on-premium.
    mm = scan.get("model_mix") or {}
    by_model = mm.get("by_model") or {}
    premium_out = sum(_int((by_model.get(f) or {}).get("out")) for f in PREMIUM_FAMILIES)
    total_model_out = sum(_int(c.get("out")) for c in by_model.values())
    premium_out_pct = _pct(premium_out, total_model_out)
    ap = mm.get("automated_premium") or {}
    auto_prem_sessions = _int(ap.get("sessions"))
    auto_prem_out = _int(ap.get("out_tokens"))
    auto_prem_cache = _int(ap.get("cache_read_tokens"))
    # API-equivalent $ avoidable by pinning automated sessions to Haiku
    # (delta on output + cache-read prices, the axes that dominate).
    prem_out_price, prem_cr_price = MODEL_PRICES["opus"]
    haiku_out_price, haiku_cr_price = MODEL_PRICES["haiku"]
    model_saving_usd = round(
        auto_prem_out / 1e6 * (prem_out_price - haiku_out_price)
        + auto_prem_cache / 1e6 * (prem_cr_price - haiku_cr_price), 2)
    marathon = mm.get("marathon_premium") or {}
    marathon_sessions = _int(marathon.get("sessions"))
    marathon_cache = _int(marathon.get("cache_read_tokens"))
    marathon_top = _int(marathon.get("top_cache_read"))

    destructive = cs.get("destructive_cmds") or []
    risky_git = sum(_int(d.get("count")) for d in destructive if d.get("label") in RISKY_GIT_LABELS)
    sleep_calls = _int(cs.get("sleep_calls"))
    hot_repos = cs.get("hot_repos_without_claudemd") or []
    claudemd_missing = len(hot_repos)
    raw_http = cs.get("raw_http_hosts") or {}

    mem = scan.get("memory_events") or {}
    sessions_with_memory = _int(mem.get("sessions_with_memory"))
    memory_rate_pct = _pct(sessions_with_memory, session_count)

    tool_errors = scan.get("tool_errors") or {}
    bash_err = tool_errors.get("Bash") or {}
    bash_ok = _int(bash_err.get("ok"))
    bash_e = _int(bash_err.get("error"))
    bash_error_pct = _pct(bash_e, bash_ok + bash_e)

    installed = set(scan.get("installed_skills") or [])
    recurring = scan.get("recurring_prompts") or []
    # "Unsaved" = a high-count repeated prompt with no matching saved command.
    unsaved = [r for r in recurring if _int(r.get("count")) >= 3]
    unsaved_count = len(unsaved)

    stuck = scan.get("stuck_loops") or []

    # Codex-native signals (absent on Claude scans → these stay 0 and gate off).
    large_exec_outputs = _int(cs.get("large_exec_outputs"))
    patch_failures = _int(cs.get("patch_failures"))

    return {
        "session_count": session_count,
        "large_exec_outputs": large_exec_outputs,
        "patch_failures": patch_failures,
        "labeled": labeled,
        "total": total,
        "finished": finished,
        "finished_pct": finished_pct,
        "coverage_pct": coverage_pct,
        "wrong_approach": wrong_approach,
        "wrong_approach_pct": wrong_approach_pct,
        "top_friction": top_friction,
        "bash_total": bash_total,
        "bypass_total": bypass_total,
        "bypass_pct": bypass_pct,
        "bypass_calls": bypass.get("bypass_calls") or {},
        "suggested_tool": bypass.get("suggested_tool") or {},
        "native_total": native_total,
        "destructive": destructive,
        "risky_git": risky_git,
        "sleep_calls": sleep_calls,
        "hot_repos": hot_repos,
        "claudemd_missing": claudemd_missing,
        "raw_http": raw_http,
        "sessions_with_memory": sessions_with_memory,
        "memory_rate_pct": memory_rate_pct,
        "bash_ok": bash_ok,
        "bash_error": bash_e,
        "bash_error_pct": bash_error_pct,
        "installed": sorted(installed),
        "bypass_out_tokens": bypass_out_tokens,
        "bypass_measured": bypass_measured,
        "bypass_native_est_tokens": bypass_native_est_tokens,
        "bypass_saved_tokens": bypass_saved_tokens,
        "error_waste_tokens": error_waste_tokens,
        "window_tokens": window_tokens,
        "premium_out_pct": premium_out_pct,
        "premium_out": premium_out,
        "total_model_out": total_model_out,
        "auto_prem_sessions": auto_prem_sessions,
        "auto_prem_out": auto_prem_out,
        "model_saving_usd": model_saving_usd,
        "marathon_sessions": marathon_sessions,
        "marathon_cache": marathon_cache,
        "marathon_top": marathon_top,
        "recurring": recurring,
        "unsaved": unsaved,
        "unsaved_count": unsaved_count,
        "stuck_loops": stuck,
    }


# --------------------------------------------------------------------------
# Verdict ladder (pick exactly ONE; top-down, stop at first match)
# --------------------------------------------------------------------------
def pick_verdict(m: dict) -> dict:
    chips: list[str] = []
    name = "Mostly healthy"
    summary = "No single dominant friction pattern — steady, with room to sharpen."

    if m["stuck_loops"]:
        top = max(m["stuck_loops"], key=lambda s: _int(s.get("count")))
        name = "Looping"
        summary = "A command was repeated in tight succession — usually a stuck moment."
        chips = [f"stuck loop ×{_int(top.get('count'))} in one session"]
    elif m["risky_git"] >= 4:
        if m["claudemd_missing"]:
            name = "Safer with structure"
            summary = "Risky git plus hot repos missing CLAUDE.md — structure would help."
            chips = [f"{m['risky_git']} risky git cmds", f"{m['claudemd_missing']} hot repos no CLAUDE.md"]
        else:
            name = "Watch the shell"
            summary = "Several risky git commands in the window — worth a safer default."
            chips = [f"{m['risky_git']} risky git cmds in {m['session_count']} sessions"]
    elif m["unsaved_count"]:
        top = max(m["unsaved"], key=lambda r: _int(r.get("count")))
        name = "Command-ready"
        summary = "A prompt is repeated often with no saved /command — prime to automate."
        chips = [f"prompt repeated ×{_int(top.get('count'))}", "no saved /command"]
    elif m["memory_rate_pct"] is not None and m["memory_rate_pct"] < 10:
        name = "Memory-light"
        summary = "Lessons rarely get saved — they don't survive the session."
        chips = [f"memory used in {m['sessions_with_memory']} of {m['session_count']} sessions ({m['memory_rate_pct']}%)"]
    elif m["coverage_pct"] is not None and m["coverage_pct"] < 30:
        name = "Under-instrumented"
        summary = "Few sessions are labeled yet — signals will sharpen with more data."
        chips = [f"{m['labeled']} of {m['total']} sessions labeled ({m['coverage_pct']}%)"]
    elif m["wrong_approach_pct"] is not None and m["wrong_approach_pct"] >= 30:
        name = "Needs a plan"
        summary = "wrong_approach shows up in a meaningful share of labeled sessions."
        chips = [f"wrong_approach in {m['wrong_approach']} of {m['labeled']} labeled ({m['wrong_approach_pct']}%)"]
    elif m["bypass_pct"] is not None and m["bypass_pct"] >= 25:
        name = "Tool-heavy"
        summary = "A lot of searching goes through the shell instead of the built-in tools."
        chips = [f"{m['bypass_pct']}% of bash via shell search ({m['bypass_total']} of {m['bash_total']})"]
    elif m["finished_pct"] is not None and m["finished_pct"] >= 85:
        name = "Sharp"
        summary = "Most labeled sessions finish what they set out to do."
        chips = [f"{m['finished_pct']}% finished ({m['finished']} of {m['labeled']} labeled)"]

    return {"name": name, "summary": summary, "evidence_chips": chips}


# --------------------------------------------------------------------------
# Primary action (pick exactly ONE; highest hit wins)
# --------------------------------------------------------------------------
def pick_primary_action(m: dict) -> dict:
    lk_inst = "learnings-keeper" in m["installed"]

    if m["stuck_loops"]:
        top = max(m["stuck_loops"], key=lambda s: _int(s.get("count")))
        return {
            "title": "Change the question, not the count",
            "phrase": "read the actual error to me and suggest a different angle",
            "why": f"A command was run ×{_int(top.get('count'))} in a few minutes in one session — a stuck signal.",
            "source": "Behavior recommendation — no install needed",
        }
    if m["risky_git"] >= 4:
        return {
            "title": "Reach for safer git defaults",
            "phrase": "use --force-with-lease and git revert next time",
            "why": f"{m['risky_git']} risky git commands (force-push / reset --hard / --no-verify) in the window.",
            "source": "Behavior recommendation — no install needed",
        }
    if m["unsaved_count"]:
        top = max(m["unsaved"], key=lambda r: _int(r.get("count")))
        say = "save my repeated prompt as a slash command"
        return {
            "title": "Save your repeated prompt as a /command",
            "phrase": say,
            "why": f"A prompt was repeated ×{_int(top.get('count'))} with no saved /command.",
            "source": "Behavior recommendation — no install needed",
        }
    if m["memory_rate_pct"] is not None and m["memory_rate_pct"] < 10:
        say = "save what we learned"
        return {
            "title": "Start saving what you learn",
            "phrase": say if lk_inst else f"(npx skills add escapemanuele/skills, then) {say}",
            "why": f"Memory was used in only {m['sessions_with_memory']} of {m['session_count']} sessions ({m['memory_rate_pct']}%).",
            "source": "learnings-keeper (sibling skill)",
        }
    if m["coverage_pct"] is not None and m["coverage_pct"] < 30:
        return {
            "title": "Let the signals sharpen",
            "phrase": "run skills-daimon again in a few days",
            "why": f"Only {m['labeled']} of {m['total']} sessions are labeled ({m['coverage_pct']}%) — more data tightens the read.",
            "source": "Behavior recommendation — no install needed",
        }
    if m["top_friction"] and m["labeled"]:
        fk, fv = m["top_friction"]
        share = _pct(_int(fv), m["labeled"])
        fix = FRICTION_FIX.get(fk, "Tighten the loop on this pattern.")
        return {
            "title": f"Address your top friction: {fk}",
            "phrase": fix,
            "why": f"{fk} in {_int(fv)} of {m['labeled']} labeled sessions ({share}%).",
            "source": "Behavior recommendation — no install needed",
        }
    if m["bypass_pct"] is not None and m["bypass_pct"] >= 25:
        return {
            "title": "Prefer the built-in search/read tools",
            "phrase": "use the built-in search and read instead of shell grep/find/cat",
            "why": f"{m['bypass_pct']}% of bash calls were shell search/read ({m['bypass_total']} of {m['bash_total']}).",
            "source": "Behavior recommendation — no install needed",
        }
    return {
        "title": "Keep the rhythm",
        "phrase": "run skills-daimon again next week to track the trend",
        "why": "No single high-priority signal fired — a healthy baseline.",
        "source": "Behavior recommendation — no install needed",
    }


# --------------------------------------------------------------------------
# Scorecard (Workflow signals) — cap ~5 rows, every row keeps its denominator
# --------------------------------------------------------------------------
def build_scorecard(m: dict) -> list[dict]:
    rows: list[dict] = []

    # File search: shell vs built-in
    if m["bypass_pct"] is not None and m["bash_total"]:
        v = "good" if m["bypass_pct"] < 10 else ("watch" if m["bypass_pct"] <= 25 else "needs_action")
        rows.append({
            "label": "File search: shell vs built-in tools",
            "value": f"{m['bypass_pct']}% via shell",
            "verdict": v,
            "note": f"{m['bypass_total']} shell vs {m['native_total']} built-in (Grep/Glob/Read)",
            "explain": "Shell grep/find/cat duplicate the built-in Grep/Glob/Read tools, which are structured and cheaper.",
            "history_key": "search_shell_pct",
            "current_number": m["bypass_pct"],
        })

    # Risky git
    v = "good" if m["risky_git"] == 0 else ("watch" if m["risky_git"] <= 3 else "needs_action")
    rows.append({
        "label": "Risky git commands",
        "value": f"{m['risky_git']} in window",
        "verdict": v,
        "note": "force-push / reset --hard / --no-verify / clean -fd (rm -rf excluded)",
        "explain": "Force-push and hard reset can drop work. --force-with-lease and git revert are safer defaults.",
        "history_key": "risky_git_count",
        "current_number": m["risky_git"],
    })

    # Recurring prompt not saved
    if m["unsaved_count"]:
        rows.append({
            "label": "Recurring prompt not saved",
            "value": f"{m['unsaved_count']} prompts",
            "verdict": "watch",
            "note": "repeated ≥3× with no matching saved command",
            "explain": "Say 'save my <task> prompt as a slash command' — Claude Code writes the command file natively.",
            "history_key": "unsaved_prompts",
            "current_number": m["unsaved_count"],
        })

    # Outcome — sessions finished
    if m["finished_pct"] is None:
        rows.append({
            "label": "Sessions finished", "value": "not enough labeled sessions yet",
            "verdict": "no_data", "note": f"{m['labeled']} of {m['total']} sessions labeled",
            "explain": "Anthropic's session labels arrive minutes after a session ends; more will appear over time.",
            "history_key": "outcome_finished_pct", "current_number": 0,
        })
    else:
        v = "good" if m["finished_pct"] >= 85 else ("watch" if m["finished_pct"] >= 70 else "needs_action")
        rows.append({
            "label": "Sessions finished", "value": f"{m['finished_pct']}% finished",
            "verdict": v, "note": f"{m['labeled']} of {m['total']} sessions labeled",
            "explain": "Share of labeled sessions that fully or mostly achieved their goal.",
            "history_key": "outcome_finished_pct", "current_number": m["finished_pct"],
        })

    # Tool error rate (Bash)
    if m["bash_error_pct"] is not None:
        v = "good" if m["bash_error_pct"] < 5 else ("watch" if m["bash_error_pct"] < 15 else "needs_action")
        rows.append({
            "label": "Bash error rate", "value": f"{m['bash_error_pct']}% Bash errors",
            "verdict": v, "note": f"{m['bash_error']} of {m['bash_ok'] + m['bash_error']} calls",
            "explain": "A high shell error rate often means commands are being guessed rather than checked first.",
            "history_key": "bash_error_pct", "current_number": m["bash_error_pct"],
        })

    # Memory usage rate
    if m["memory_rate_pct"] is None:
        rows.append({
            "label": "Memory usage rate", "value": "not enough data", "verdict": "no_data",
            "note": "no sessions in window", "explain": "Save lessons with 'save what we learned' (learnings-keeper).",
            "history_key": "memory_rate_pct", "current_number": 0,
        })
    else:
        v = "good" if m["memory_rate_pct"] >= 30 else ("watch" if m["memory_rate_pct"] >= 10 else "needs_action")
        rows.append({
            "label": "Memory usage rate", "value": f"{m['memory_rate_pct']}% of sessions",
            "verdict": v, "note": f"{m['sessions_with_memory']} of {m['session_count']} sessions",
            "explain": "After a useful session, say 'save what we learned' — the learnings-keeper skill captures it.",
            "history_key": "memory_rate_pct", "current_number": m["memory_rate_pct"],
        })

    # Model mix — flag automation running on a premium model.
    if m.get("premium_out_pct") is not None:
        if m.get("model_saving_usd", 0) >= 5:
            v = "needs_action"
            explain = (f"{m['auto_prem_sessions']} automated-looking sessions ran on a premium model "
                       f"(~${m['model_saving_usd']:,.0f} API-equivalent avoidable). Pin --model in the "
                       "scheduled invocation; Haiku/Sonnet handle templated jobs fine.")
        elif m["premium_out_pct"] >= 90:
            v = "watch"
            explain = ("Nearly all output runs on premium models. Fine for hard work — consider "
                       "Sonnet for routine edits and Haiku for mechanical subagents.")
        else:
            v = "good"
            explain = "Model choice looks deliberate — premium where it matters."
        rows.append({
            "label": "Premium-model output share",
            "value": f"{m['premium_out_pct']}% of output",
            "verdict": v,
            "note": f"{m['premium_out']:,} of {m['total_model_out']:,} output tokens on opus/fable",
            "explain": explain,
            "history_key": "premium_out_pct", "current_number": m["premium_out_pct"],
        })

    return rows[:7]


# --------------------------------------------------------------------------
# Archetype — derived from work_recap.mix + signals (deterministic pick)
# --------------------------------------------------------------------------
def pick_archetype(scan: dict, m: dict) -> dict:
    recap = scan.get("work_recap") or {}
    mix = recap.get("mix") or {}
    dev = _int(mix.get("dev"))
    writing = _int(mix.get("writing"))
    data = _int(mix.get("data"))
    ops = _int(mix.get("ops"))

    if not mix:
        title, why = "The Pathfinder", "Not enough work-mix signal yet — exploring broadly."
    elif data >= max(dev, writing, ops) and data >= 25:
        title, why = "The Data Cartographer", f"Your work mix leaned data ({data}%)."
    elif ops >= max(dev, writing, data) and ops >= 25:
        title, why = "The Ops Ranger", f"Your work mix leaned ops ({ops}%)."
    elif dev and writing and abs(dev - writing) <= 25 and writing >= 25:
        title, why = "The Builder-Scribe", f"Your work mix was {dev}% dev and {writing}% writing."
    elif writing >= max(dev, data, ops):
        title, why = "The Scribe", f"Your work mix leaned writing ({writing}%)."
    else:
        title, why = "The Refactor Druid", f"Your work mix was dev-dominant ({dev}%)."

    strength = "You move from concept to working artifact quickly."
    watch_out = "Research and debug loops are where momentum can leak."
    if m["bypass_pct"] is not None and m["bypass_pct"] >= 25:
        watch_out = f"Shell search ({m['bypass_pct']}%) may be slower than the built-in tools."
    elif m["risky_git"] >= 4:
        watch_out = f"{m['risky_git']} risky git commands — a safer default would protect work."
    next_ritual = "Before coding, ask Claude for a 5-step plan and one risky assumption."
    if m["memory_rate_pct"] is not None and m["memory_rate_pct"] < 10:
        next_ritual = "End useful sessions with 'save what we learned' so lessons compound."

    return {
        "title": title,
        "tagline": "You turn rough ideas into working artifacts.",
        "why": why,
        "strength": strength,
        "watch_out": watch_out,
        "next_ritual": next_ritual,
    }


# --------------------------------------------------------------------------
# Coaching cards — ≤3, threshold-gated, every point cites a hard count
# --------------------------------------------------------------------------
_NO_INSTALL = "No install needed — this is a behavior change."


def build_coaching(m: dict) -> list[dict]:
    """Threshold-gated coaching cards (cap 3). Each card carries:
    title · hard_count (the cited number) · saw · matters · better · handoff.
    The hard_count is the Evidence line in markdown; saw/matters/better map to
    the renderer's What-we-saw / Why-it-matters / Try-this. Every card cites a count."""
    cards: list[dict] = []

    # Stuck loop (highest priority when present)
    if m["stuck_loops"]:
        top = max(m["stuck_loops"], key=lambda s: _int(s.get("count")))
        n = _int(top.get("count"))
        cards.append({
            "title": "A stuck loop showed up",
            "hard_count": f"{n} identical commands in a few minutes (one session)",
            "saw": f"You ran `{top.get('command_summary', 'a command')}` {n}× in a few minutes.",
            "matters": "Usually means stuck — same command, no result, run again.",
            "better": "Change the question, not the count: read the error properly, or try a different angle.",
            "handoff": _NO_INSTALL,
        })

    # Recurring prompt unsaved (clearest single win when present)
    if m["unsaved_count"]:
        top = max(m["unsaved"], key=lambda r: _int(r.get("count")))
        n = _int(top.get("count"))
        cards.append({
            "title": "A repeated prompt has no saved command",
            "hard_count": f"a prompt repeated ×{n} across sessions",
            "saw": "The same instruction is retyped across sessions with no saved /command.",
            "matters": "Retyping is slower and the wording drifts run to run.",
            "better": 'Say "save my <task> prompt as a slash command" — no install needed.',
            "handoff": _NO_INSTALL,
        })

    # Automation running on a premium model (measured $ delta, floor $5)
    if m.get("model_saving_usd", 0) >= 5:
        cards.append({
            "title": "Scheduled jobs are burning a premium model",
            "hard_count": (f"{m['auto_prem_sessions']} automated-looking sessions, "
                           f"{_fmt_tokens(m['auto_prem_out'])} output tokens on opus/fable "
                           f"(~${m['model_saving_usd']:,.0f} API-equivalent avoidable)"),
            "saw": "Sessions that start with an identical repeated prompt — cron jobs, "
                   "pipelines, graders — ran on a top-tier model.",
            "matters": "Templated jobs don't need frontier reasoning; they inherit the "
                       "default model unless pinned, and eat your limits at premium weight.",
            "better": 'Add --model (e.g. claude-haiku-4-5-20251001 or claude-sonnet-5) '
                      'to the scheduled claude -p invocation.',
            "handoff": _NO_INSTALL,
        })

    # Marathon sessions riding a premium context (cache reads are the cost)
    if m.get("marathon_sessions", 0) >= 1:
        n = m["marathon_sessions"]
        word = "session" if n == 1 else "sessions"
        orch = {"feature", "bug"} & set(m.get("installed", []))
        if orch:
            better = ("Delegate the grind: /feature and /bug run exploration, edits, and "
                      "tests in cheaper-model subagents, so only planning and review ride "
                      "the premium context.")
        else:
            better = ("Delegate mechanical work (exploring, editing, running tests) to "
                      "subagents on a cheaper model — the premium context stays small; "
                      "or switch /model for the grind stretches.")
        cards.append({
            "title": "Big sessions ride a premium context",
            "hard_count": (f"{n} {word} with ≥50M premium cache-read tokens each "
                           f"(largest {_fmt_tokens(m.get('marathon_top', 0))})"),
            "saw": "Long coding sessions kept the whole working context on a top-tier "
                   "model — every turn re-reads it at premium weight.",
            "matters": "Cache reads dominate the cost of marathon sessions; that is what "
                       "hits usage limits, not the answers themselves.",
            "better": better,
            "handoff": "orchestration (/feature, /bug)" if orch else _NO_INSTALL,
        })

    # Native-tool bypass (≥10% AND ≥30 calls)
    if m["bypass_pct"] is not None and m["bypass_pct"] >= 10 and m["bypass_total"] >= 30:
        parts = ", ".join(f"{k} ×{v}" for k, v in list(m["bypass_calls"].items())[:4])
        cards.append({
            "title": "Searching the hard way",
            "hard_count": f"{m['bypass_total']} of {m['bash_total']} bash calls were shell search/read ({m['bypass_pct']}%)",
            "saw": f"Shell {parts} stood in for the built-in tools.",
            "matters": "Grep/Glob/Read are structured, cheaper, and don't spawn a shell.",
            "better": "Prefer the built-in search/read tools.",
            "handoff": _NO_INSTALL,
        })

    # Risky git (≥4)
    if m["risky_git"] >= 4:
        riskiest = max(
            (d for d in m["destructive"] if d.get("label") in RISKY_GIT_LABELS),
            key=lambda d: _int(d.get("count")), default=None,
        )
        lbl = riskiest.get("label") if riskiest else "risky git"
        cards.append({
            "title": "Risky git commands add up",
            "hard_count": f"{m['risky_git']} risky git commands in the window",
            "saw": f"`{lbl}` showed up repeatedly.",
            "matters": "These can drop work or skip safety checks.",
            "better": "Use `--force-with-lease`, `git revert`, or fix the hook instead of `--no-verify`.",
            "handoff": _NO_INSTALL,
        })

    # Sleep-polling (≥5)
    if m["sleep_calls"] >= 5:
        cards.append({
            "title": "Fixed sleeps instead of a real wait",
            "hard_count": f"{m['sleep_calls']} foreground sleep calls",
            "saw": "Fixed `sleep` calls were used to wait on something.",
            "matters": "They waste time and can race — polling instead of waiting for the condition.",
            "better": "Use a proper wait or a background job that notifies on completion.",
            "handoff": _NO_INSTALL,
        })

    # Top friction (≥30% of labeled)
    if m["top_friction"] and m["labeled"]:
        fk, fv = m["top_friction"]
        share = _pct(_int(fv), m["labeled"])
        if share is not None and share >= 30 and fk in FRICTION_FIX:
            cards.append({
                "title": f"Top friction: {fk}",
                "hard_count": f"{fk} in {_int(fv)} of {m['labeled']} labeled sessions ({share}%)",
                "saw": f"{fk} recurs across a meaningful share of labeled sessions.",
                "matters": "Frequent enough to be worth a small habit change.",
                "better": FRICTION_FIX[fk],
                "handoff": _NO_INSTALL,
            })

    # Hot repo missing CLAUDE.md
    if m["hot_repos"]:
        top = m["hot_repos"][0]
        # Basename only — never put the full filesystem path in a disk artifact.
        repo_name = _basename(top.get("path", "")) or "a repo"
        cards.append({
            "title": "A hot repo has no CLAUDE.md",
            "hard_count": f"{_int(top.get('sessions'))} sessions in a repo with no CLAUDE.md",
            "saw": f"`{repo_name}` is used a lot but has no CLAUDE.md.",
            "matters": "Per-repo context gets re-explained every session.",
            "better": "Add a short CLAUDE.md so context persists across sessions.",
            "handoff": _NO_INSTALL,
        })

    # Codex-native: apply_patch failures (gated; 0 on Claude scans).
    if m.get("patch_failures", 0) >= 2:
        cards.append({
            "title": "Patches keep failing to apply",
            "hard_count": f"{m['patch_failures']} apply_patch attempts failed in the window",
            "saw": "Edits were rejected because the patch context didn't match the file.",
            "matters": "Each failed patch burns a turn and re-sends the file to retry.",
            "better": "Read the exact lines right before editing so the patch context is current.",
            "handoff": _NO_INSTALL,
        })

    return cards[:3]


def coaching_for_render(cards: list[dict]) -> list[dict]:
    """Map internal coaching cards to render_report's keys. The hard count is the
    Evidence line (charter: every coaching point cites a hard count); the narrative
    saw→What we saw, costs→Why it matters, better→Try this."""
    return [
        {"title": c["title"], "evidence": c.get("hard_count", ""), "saw": c.get("saw", ""),
         "costs": c.get("matters", ""), "better": c.get("better", "")}
        for c in cards
    ]


# --------------------------------------------------------------------------
# History snapshot (numeric only)
# --------------------------------------------------------------------------
def build_history_snapshot(scan: dict, m: dict, scorecard: list[dict], archetype: dict) -> dict:
    recap = scan.get("work_recap") or {}
    mix = {k: _int(v) for k, v in (recap.get("mix") or {}).items()}
    sc = {
        "outcome_finished_pct": _int(m["finished_pct"]),
        "bash_error_pct": float(m["bash_error_pct"] or 0),
        "memory_rate_pct": _int(m["memory_rate_pct"]),
        "search_shell_pct": _int(m["bypass_pct"]),
        "risky_git_count": _int(m["risky_git"]),
        "claudemd_missing": _int(m["claudemd_missing"]),
        "unsaved_prompts": _int(m["unsaved_count"]),
        "token_saved_estimate": _int(m.get("bypass_saved_tokens")) + _int(m.get("error_waste_tokens")),
        "premium_out_pct": _int(m.get("premium_out_pct")),
    }
    return {
        "date": (scan.get("date") or ""),  # caller stamps today's date
        "window_days": _int(scan.get("max_age_days")) or 28,
        "sessions": m["session_count"],
        "labeled": m["labeled"],
        "scorecard": sc,
        "archetype": archetype["title"],
        "work_mix": mix,
    }


# --------------------------------------------------------------------------
# Markdown report skeleton (recommendations/gaps left as placeholders)
# --------------------------------------------------------------------------
def build_token_savings(m: dict) -> dict | None:
    """The daimon-way comparison: measured waste vs what the same work would
    have cost with the cheaper habit. All numbers measured from real tool
    output sizes (tokens ≈ chars/4); estimates are labeled as such. Returns
    None when nothing meaningful was measured — never a fabricated number."""
    saved = _int(m.get("bypass_saved_tokens")) + _int(m.get("error_waste_tokens"))
    model_usd = float(m.get("model_saving_usd") or 0)
    if saved < 1000 and model_usd < 5:
        return None
    bypass_saved = _int(m.get("bypass_saved_tokens"))
    error_waste = _int(m.get("error_waste_tokens"))
    window = _int(m.get("window_tokens"))
    out = {
        "estimated_saved_tokens": saved,
        "bypass_measured_tokens": _int(m.get("bypass_out_tokens")),
        "bypass_native_est_tokens": _int(m.get("bypass_native_est_tokens")),
        "bypass_saved_tokens": bypass_saved,
        "error_waste_tokens": error_waste,
        "window_tokens": window,
    }
    pct = _pct(saved, window)
    if pct is not None:
        out["pct_of_window"] = pct
    parts = []
    if bypass_saved:
        parts.append(
            f"shell search/read output measured {_fmt_tokens(m.get('bypass_out_tokens'))} tokens "
            f"vs ~{_fmt_tokens(m.get('bypass_native_est_tokens'))} if the same lookups used Grep/Glob/Read"
        )
    if error_waste:
        another = "another " if parts else ""
        parts.append(f"errored bash calls burned {another}{_fmt_tokens(error_waste)}")
    pct_str = f" (~{pct}% of the {_fmt_tokens(window)} in this window)" if pct else ""
    if saved >= 1000:
        out["headline"] = (
            f"Doing it the daimon way would have saved ~{_fmt_tokens(saved)} tokens{pct_str}: "
            + "; ".join(parts) + "."
        )
    else:
        out["headline"] = ""
    if model_usd >= 5:
        out["model_saving_usd"] = model_usd
        out["model_headline"] = (
            f"{_int(m.get('auto_prem_sessions'))} automated-looking sessions ran on a premium "
            f"model — ~${model_usd:,.0f} API-equivalent avoidable by pinning them to Haiku "
            f"(--model in the scheduled invocation)."
        )
        if not out["headline"]:
            out["headline"] = out["model_headline"]
        else:
            out["headline"] += " " + out["model_headline"]
    return out


def build_token_tips(m: dict) -> list[dict]:
    """Token-cost tips, each gated on a real count and naming a cheaper path.
    Only genuine waste with a clear alternative — never count-free editorializing.
    Cap 3. Each tip: {title, evidence (hard count), tip}."""
    tips: list[dict] = []

    # Shell search/read pipes whole files into context; the built-ins return
    # only what matched.
    if m["bypass_total"] >= 20 and m["bash_total"]:
        calls = m["bypass_calls"] or {}
        top = ", ".join(f"{k}×{v}" for k, v in
                        sorted(calls.items(), key=lambda kv: -_int(kv[1]))[:3])
        evidence = (f"{m['bypass_total']} of {m['bash_total']} bash calls were "
                    f"shell search/read ({m['bypass_pct']}%)" + (f" — {top}" if top else ""))
        if m.get("bypass_saved_tokens"):
            evidence += (f"; measured {_fmt_tokens(m['bypass_out_tokens'])} tokens of output, "
                         f"~{_fmt_tokens(m['bypass_saved_tokens'])} avoidable")
        tips.append({
            "title": "Search with the built-in tools",
            "evidence": evidence,
            "tip": "Shell grep/cat/find stream entire files into context; Grep/Glob/Read "
                   "return just the matches. Same answer, a fraction of the tokens.",
        })

    # A stuck loop re-streams the same output every iteration.
    if m["stuck_loops"]:
        top = max(m["stuck_loops"], key=lambda s: _int(s.get("count")))
        n = len(m["stuck_loops"])
        loops = "loop" if n == 1 else "loops"
        tips.append({
            "title": "Break stuck command loops sooner",
            "evidence": f"{n} stuck {loops}; one command ran ×{_int(top.get('count'))} "
                        "in tight succession",
            "tip": "Each repeat re-streams the same output into context. After the second "
                   "identical failure, change the approach instead of retrying.",
        })

    # Codex-native: oversized command outputs flood context (gated; 0 on Claude).
    if m.get("large_exec_outputs", 0) >= 3:
        tips.append({
            "title": "Tame oversized command outputs",
            "evidence": f"{m['large_exec_outputs']} commands returned 10k+ tokens of output",
            "tip": "Whole-file dumps and noisy logs land in context in full. Pipe through "
                   "head/grep/sed or cap with a smaller max_output_tokens to keep only what matters.",
        })

    # A failed command still costs its output, then you pay again on the retry.
    if m["bash_error"] >= 40:
        evidence = (f"{m['bash_error']} of {m['bash_ok'] + m['bash_error']} bash calls "
                    f"errored ({m['bash_error_pct']}%)")
        if m.get("error_waste_tokens"):
            evidence += f" — {_fmt_tokens(m['error_waste_tokens'])} tokens of error output"
        tips.append({
            "title": "Cut the shell error rate",
            "evidence": evidence,
            "tip": "Every error lands its output in context, then the retry costs it again. "
                   "Check the path/flags (ls, --help) before running.",
        })

    return tips[:3]


def build_markdown(scan: dict, m: dict, verdict: dict, archetype: dict,
                   primary: dict, scorecard: list[dict], coaching: list[dict],
                   token_tips: list[dict], token_savings: dict | None = None) -> str:
    days = _int(scan.get("max_age_days")) or 28
    recap = scan.get("work_recap") or {}
    mix = recap.get("mix") or {}
    L: list[str] = []
    L.append(f"# Skills Daimon — last {days} days\n")
    L.append("<!-- VISUAL REPORT: prepend the file:// URL from render_report.py here -->\n")

    L.append(f"## 🏛  Verdict: {verdict['name']}\n")
    L.append(verdict["summary"] + "\n")
    if verdict["evidence_chips"]:
        L.append("**Evidence:** " + " · ".join(verdict["evidence_chips"]))
    L.append(f"**Next move:** *{primary['phrase']}*\n")
    L.append("---\n")

    L.append(f"## ✨ Your archetype: {archetype['title']}\n")
    L.append(f"> {archetype['tagline']}\n")
    L.append(f"- **Why this title:** {archetype['why']}")
    L.append(f"- **Strength:** {archetype['strength']}")
    L.append(f"- **Watch-out:** {archetype['watch_out']}")
    L.append(f"- **Next ritual:** {archetype['next_ritual']}\n")
    L.append("---\n")

    L.append("## 🎯 Primary next action\n")
    L.append(f"**{primary['title']}**\n")
    L.append("Say:")
    L.append(f"> *\"{primary['phrase']}\"*\n")
    L.append(f"**Why:** {primary['why']}\n")
    L.append(f"**Source:** {primary['source']}\n")
    L.append("---\n")

    if mix:
        mix_str = ", ".join(f"{k} {v}%" for k, v in mix.items())
        L.append("## 🧭 What you've been working on\n")
        L.append(f"Work mix: {mix_str}.\n")
        L.append("| Project | Sessions | Tokens | Shipped | Focus |")
        L.append("|---|---|---|---|---|")
        for p in (recap.get("top_projects") or [])[:5]:
            base = _basename(p.get("path", "")) or "(repo)"
            shipped = f"{_int(p.get('commits'))}c/{_int(p.get('pushes'))}p"
            L.append(f"| {base} | {_int(p.get('sessions'))} | {_int(p.get('tokens'))} | {shipped} | {p.get('kind', '')} |")
        L.append("\n---\n")

    L.append("## ✨ Catalog-backed recommendations\n")
    L.append(REC_PLACEHOLDER + "\n")
    L.append("---\n")

    L.append("## 🩺 Workflow signals\n")
    L.append("| | Signal | Now | Verdict |")
    L.append("|---|---|---|---|")
    emoji = {"good": "🟢", "watch": "🟡", "needs_action": "🔴", "no_data": "⚪"}
    vlabel = {"good": "Good", "watch": "Watch", "needs_action": "Needs action", "no_data": "No data"}
    for r in scorecard:
        e = emoji.get(r["verdict"], "⚪")
        L.append(f"| {e} | {r['label']} | {r['value']} | {vlabel.get(r['verdict'], '')} |")
    L.append("\n---\n")

    if coaching:
        L.append("# ⚑ Coaching — small habits worth changing\n")
        for c in coaching:
            L.append(f"### {c['title']}")
            L.append(f"**Evidence:** {c['hard_count']}")
            L.append(f"**What we saw:** {c['saw']}")
            L.append(f"**Why it matters:** {c['matters']}")
            L.append(f"**Try this:** {c['better']}")
            L.append(f"**Handoff:** {c.get('handoff', 'No install needed.')}\n")
        L.append("---\n")

    if token_tips:
        L.append("## 💸 Trim token usage\n")
        if token_savings:
            L.append(f"**{token_savings['headline']}**\n")
        L.append("Cheaper habits that keep context small — each tied to a real count.\n")
        for t in token_tips:
            L.append(f"### {t['title']}")
            L.append(f"**Evidence:** {t['evidence']}")
            L.append(f"**Try this:** {t['tip']}\n")
        L.append("---\n")

    L.append("## 🛠️ Worth building yourself\n")
    L.append(GAP_PLACEHOLDER + "\n")
    L.append("---\n")

    L.append("## 📈 Trends\n")
    L.append("<!-- TRENDS: render only when ≥3 distinct history days exist (history.py read). -->")
    L.append("*Trends unlock after 3 distinct report days. Today's numeric snapshot has been saved.*\n")

    return "\n".join(L)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def analyze(scan: dict) -> dict:
    m = compute_metrics(scan)
    verdict = pick_verdict(m)
    primary = pick_primary_action(m)
    verdict["next_phrase"] = primary["phrase"]
    scorecard = build_scorecard(m)
    archetype = pick_archetype(scan, m)
    coaching = build_coaching(m)
    token_tips = build_token_tips(m)
    token_savings = build_token_savings(m)
    history_snapshot = build_history_snapshot(scan, m, scorecard, archetype)
    markdown = build_markdown(scan, m, verdict, archetype, primary, scorecard,
                              coaching, token_tips, token_savings)

    meta = {
        "days": _int(scan.get("max_age_days")) or 28,
        "sessions": m["session_count"],
        "projects": len(scan.get("projects") or {}),
        "date": scan.get("date") or "",
        "catalogs": [c.get("name") for c in (scan.get("available_catalogs") or [])],
    }

    # The analysis output IS the render payload (so `render_report.py
    # analysis.json` works directly) plus a few extra keys the renderer ignores
    # (markdown_report, history_snapshot, html_payload). recommendations/gaps are
    # EMPTY here — Claude fills them from real catalog hits. Raw scan blocks are
    # passed through so gamify.py sees the same evidence.
    payload = {
        "meta": meta,
        "verdict": verdict,
        "archetype": archetype,
        "primary_action": primary,
        "recommendations": [],
        "gaps": [],
        "coaching": coaching_for_render(coaching),
        "token_tips": token_tips,
        "token_savings": token_savings,
        "scorecard": scorecard,
        "work_recap": scan.get("work_recap") or {},
        "charts": {
            "tool_use_top": scan.get("tool_use_top") or {},
            "bash_verbs_top": scan.get("bash_verbs_top") or {},
        },
        # Raw pass-through REQUIRED for gamify.
        "coaching_signals": scan.get("coaching_signals") or {},
        "outcomes": scan.get("outcomes") or {},
        "memory_events": scan.get("memory_events") or {},
        "recurring_prompts": scan.get("recurring_prompts") or [],
        "tool_errors": scan.get("tool_errors") or {},
        "completion": scan.get("completion") or {},
        "stuck_loops": scan.get("stuck_loops") or [],
        "installed_skills": scan.get("installed_skills") or [],
    }

    # Numeric-only Daimon Grove snapshot for history (best-effort; gamify is
    # optional and must never break the analysis). numeric_game_history_snapshot
    # already strips non-numeric values; history.py re-enforces it on write.
    try:
        import gamify  # noqa: E402
        state = gamify.build_game_state(
            payload, history_snapshots=None,
            today=scan.get("date") or None,
            window_days=history_snapshot["window_days"],
        )
        history_snapshot["game"] = gamify.numeric_game_history_snapshot(state)
    except Exception:
        history_snapshot["game"] = {}

    out = dict(payload)
    out["markdown_report"] = markdown
    out["history_snapshot"] = history_snapshot
    out["html_payload"] = payload      # spec-compatible alias
    out["coaching_cards"] = coaching   # internal shape (hard_count/saw/matters)
    return out


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", "--stdin"):
        scan = json.loads(Path(sys.argv[1]).read_text())
    else:
        scan = json.loads(sys.stdin.read())
    out = analyze(scan)
    # Belt-and-braces: redact every string leaf before emitting.
    out = redact_in(out)
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
