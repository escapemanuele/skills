# Analysis rules — verdict, primary action, scorecard, archetype, coaching

Deterministic rules for turning scan output into the report. Every threshold
here is intended to be implemented by `bin/analyze.py`; this file is the
human-readable source of truth and the fallback when reasoning by hand.

All rates carry denominators. Outcome rates use `outcomes.coverage.labeled`,
**never** total sessions. Memory rate uses `memory_events.sessions_with_memory /
session_count`. Risky-git ignores `rm -rf /tmp` normal cleanup.

## Verdict ladder (pick exactly ONE; walk top-down, stop at first match)

The hero verdict is a single name, not a numeric score.

1. `stuck_loops` non-empty → **Looping**
2. Risky-git count ≥ 4 → **Watch the shell** (or **Safer with structure** if combined with hot repos missing CLAUDE.md)
3. Recurring prompt count ≥ 3 AND no matching `installed_skills` → **Command-ready**
4. Memory rate < 10% AND a reusable lesson is visible → **Memory-light**
5. `outcomes.coverage.labeled / total` < 30% → **Under-instrumented**
6. Top friction = `wrong_approach` in ≥ 30% of labeled sessions → **Needs a plan**
7. Native-tool bypass ≥ 25% → **Tool-heavy**
8. Finished ≥ 85% of labeled AND no red flag above → **Sharp** (use **Steady** when the margin is smaller)
9. Default → **Mostly healthy**

The verdict appears in the hero card with a one-sentence summary explaining the
strongest pattern, 2–4 evidence chips, and exactly one *Next move*.

## Primary action priority (pick exactly ONE; highest hit wins)

1. Stuck-loop present → *"change the question, not the count"* (behavior advice; no install)
2. Risky-git ≥ 4 → *"use `--force-with-lease` and `git revert` next time"* (behavior; no install)
3. Repeated prompt unsaved → *"save this prompt as a slash command"* (behavior; no install — Claude Code writes slash commands natively)
4. Memory rate < 10% with reusable lessons → *"save what we learned"* → **learnings-keeper**
5. Coverage < 30% → *"run skills-daimon again in a few days; signals will sharpen"* (no install)
6. Top recurring friction → matching behavior or sibling-skill action
7. Native-tool bypass ≥ 25% → *"prefer the built-in search/read"* (behavior)

The primary-action card carries: title · exact phrase to say · why (hard-count
evidence) · source (`Behavior recommendation` / `learnings-keeper` /
catalog-backed skill name) · one paragraph of why-it-matters.

## Scorecard thresholds (Workflow signals)

Score each signal that has a quality axis; skip any with no data. `verdict` is
one of `good` / `watch` / `needs_action` / `no_data`. Cap at ~5 rows. Prefer
showing at least one `good` row when something genuinely is fine. When data is
missing, prefer a `no_data` row with `value: "not enough data"` over omission.

- **File search (shell vs built-in)** — from `coaching_signals.native_tool_bypass`. `value` = "<pct>% via shell", `note` = "<bypass_total> shell vs <Grep+Glob+Read> built-in". `<10%` → `good`, `10–25%` → `watch`, `>25%` → `needs_action`.
- **Risky git** — count `git push --force` / `git reset --hard` / `--no-verify` from `destructive_cmds` (ignore `rm -rf`). `0` → `good` (show it), `1–3` → `watch`, `4+` → `needs_action`.
- **Raw HTTP to a tool-backed host** — only for hosts in `raw_http_hosts` that actually have a CLI/MCP (you judge, e.g. `teamcity.a8c.com`). Any such host → `watch`. `value` = "<host> ×N".
- **Recurring prompt not saved** — recurring prompts (count ≥ 3) with no matching `installed_skills`. Any → `watch`. `value` = "N prompts". In `explain`, name the fix: *"Say 'save my <task> prompt as a slash command' — Claude Code writes the command file natively, no install needed."*
- **Outcome — sessions finished** — from `outcomes.by_facet` + `outcomes.coverage`. `(fully_achieved + mostly_achieved) / labeled`. `≥85%` → `good`, `70–85%` → `watch`, `<70%` → `needs_action`. `value` = "<pct>% finished". `note` MUST include coverage: *"<labeled> of <total> sessions labeled"*. When `labeled` is 0 → `no_data`, `value: "not enough labeled sessions yet"`.
- **Tool error rate (Bash)** — from `tool_errors.Bash`. Rate = `error / (ok + error)`. `<5%` → `good`, `5–15%` → `watch`, `≥15%` → `needs_action`. `value` = "<pct>% Bash errors", `note` = "<error> of <ok+error> calls".
- **Memory usage rate** — `memory_events.sessions_with_memory / session_count`. `≥30%` → `good`, `10–30%` → `watch`, `<10%` → `needs_action`. In `explain`, name the fix: *"After a useful session, just say **'save what we learned'** — the `learnings-keeper` skill captures it"* (prefix `npx skills add escapemanuele/skills` if not installed).

Each row carries a `note` (denominator) and an `explain` (1–2 plain sentences for
the expand-on-click body). Optional `history_key` + `current_number` let the
renderer draw a sparkline from `history.jsonl`.

## Coaching cards (≤3, hard cap)

After recommendations, read `coaching_signals` and surface a few high-signal
habits. Same bar as recommendations: **every point cites a hard count**.

**Guard rules (apply to every point):**
1. **Cap at 3.** Pick the highest-signal ones.
2. **Every point cites a hard count** from `coaching_signals` / `outcomes`; every rate carries its denominator. No count → no point.
3. **Only flag a habit whose signal clears its threshold:** native-tool bypass **≥10% AND ≥30 calls**; risky git **≥4**; sleep-polling **≥5**; recurring-prompt-unsaved **count ≥3**; top friction **≥30% of `coverage.labeled`**. Under threshold → omitted, not softened.
4. If **no** signal clears threshold, omit the whole coaching section. Silence beats manufactured advice.

Card shape: **Evidence (hard count) → What we saw → Why it matters → Try this →
Handoff**. `Handoff` names the sibling skill that closes the loop
(`learnings-keeper`) or *"No install needed — behavior change."*

Signals to build from (skip any under threshold):

**Anti-patterns:**
- **Native-tool bypass** — `native_tool_bypass.bypass_total` ≥10% of `bash_total` AND ≥30 calls. Quote the breakdown (`grep ×N`, `find ×N`, `cat ×N`) + `suggested_tool` mapping (Grep/Glob/Read); contrast with `native_tool_use`. Fix: prefer native tools.
- **Raw HTTP to a tool-backed host** — for each `raw_http_hosts` entry with a known CLI/MCP (e.g. `teamcity.a8c.com` → `teamcity` CLI). Skip localhost / one-off hosts.
- **Destructive commands** — surface riskiest from `destructive_cmds` (`git push --force`, `git reset --hard`, `--no-verify`) with count + safer alternative (`--force-with-lease`, `git revert`, fix the hook). Skip `rm -rf` against `/tmp`.
- **Sleep-polling** — `sleep_calls` ≥ 5 → suggest a proper wait/background job.

**Missing patterns:**
- **Recurring prompt with no saved command** — `recurring_prompts` (count ≥ 3) with no matching `installed_skills`. Fix is a behavior change, no install: *"Just say: **\"save my <task> prompt as a slash command\"** — Claude Code writes the `~/.claude/commands/<name>.md` file natively."*
  Usually the clearest single win — prefer it as one of the 3 when present.
- **Hot repo without CLAUDE.md** — for each `hot_repos_without_claudemd` entry, suggest adding `CLAUDE.md`.
- **Plan mode / subagents / memory** — only if a signal supports it. No generic listing.
- **Top recurring friction** (`outcomes.friction_sessions`) — when a friction type appears in **≥30%** of `coverage.labeled`, surface it with fix + count:
  - `wrong_approach` → *"Plan before coding. Try 'let's plan this first' on big changes."*
  - `buggy_code` → *"Smaller diffs; ask Claude to add a quick test alongside the change."*
  - `misunderstood_request` → *"Open with one sentence of context, then the ask."*
  - `user_rejected_action` → *"Have Claude show the plan before making changes."*
  Citation includes both denominator and intensity when they differ — e.g. *"wrong_approach in 19 of 57 labeled sessions (33%); 23 total events."*
  **When `learnings-keeper` is installed**, also call its lookup for past notes:
  ```bash
  python3 ${CLAUDE_SKILL_DIR}/../learnings-keeper/bin/lookup.py --query "<friction_term>" --repo "<repo basename>" --limit 3
  ```
  On hits, append to *Try this*: *"You wrote a note on this on <date> — see `<title>`."*

**Stuck-loop coaching (when `stuck_loops` non-empty):** surface the highest-`count`
entry as one coaching point —
- What we saw: "You ran `<command_summary>` <count>× in a few minutes in one session."
- Why it matters: "Usually means stuck — same command, no result, run again."
- Try this: "Change the question, not the count: read the error properly, or try a different angle."

Privacy: `stuck_loops[].command` is for this run's coaching only — never into the
history snapshot. The redacted summary may appear in the markdown report; the raw
command never in `history.jsonl`.

## Token savings estimate (💸 section lead)

`build_token_savings` compares measured waste against the daimon way, from real
tool-result sizes (tokens ≈ chars/4):

- **Search bypass**: output of shell grep/rg/find/awk calls vs the same number of
  lookups at the measured Grep/Glob average (fallback 300 tokens/call when <10
  native calls measured). Saved = measured − estimated, floored at 0. cat/head→Read
  is cost-neutral and never counted.
- **Error waste**: output of errored Bash calls — pure context cost.
- Headline renders only when the total ≥1000 tokens; percentages carry the window
  token denominator. Never a fabricated number — no measurement, no banner.

## Worth building yourself (gaps)

Jobs the user does often where no catalog skill fits. One-line bullet each, job
tag bold-leading, evidence inline, ending in `npx skills init <name>` **only when
skills.sh is in `available_catalogs`** (npx present); otherwise plain description.
No Evidence/Install/Confidence blocks, no confidence dots, max 3 entries.

## Archetype (playful title)

Give one short, mythic/playful title from `work_recap.mix` + tool/MCP signals.
Five-part shape — `title`, `tagline`, `why` (derivable from `work_recap.mix`,
no hand-waving), `strength`, `watch_out` (connect to a real signal), `next_ritual`.

Suggested (extend if a better fit is obvious; keep the tone):
- **The Data Cartographer** — heavy SQL/Trino/data MCP.
- **The Refactor Druid** — dev-dominant, lots of Edit + git on code.
- **The Shell Whisperer** — very high bash/verb volume.
- **The Scribe** — writing-dominant (prose `.md` edits, long prompts).
- **The Builder-Scribe** — real mix of dev + writing (~60/35).
- **The Orchestrator** — heavy subagent/Task or MCP-tool fan-out.
- **The Pathfinder** — lots of search/explore (Grep/Glob/Read, codebase tours).
- **The Debug Alchemist** — heavy `outcomes.primary_success == good_debugging`.
- **The Test Oracle** — high pytest/jest/vitest + Edit/Write on tests.
- **The Ops Ranger** — calendar/gmail/slack MCP-heavy.
- **The Release Warden** — high commits/pushes per session + CI churn.

```json
"archetype": {
  "title": "The Builder-Scribe",
  "tagline": "You turn rough ideas into working artifacts.",
  "why": "Your work mix was 60% dev and 34% writing.",
  "strength": "You move from concept to working code quickly.",
  "watch_out": "Research/debug loops are where momentum may leak.",
  "next_ritual": "Before coding, ask Claude for a 5-step plan and one risky assumption."
}
```
