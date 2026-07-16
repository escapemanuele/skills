# Pipeline — operational detail

All bundled files live under `${CLAUDE_SKILL_DIR}` (the skill's own directory).
If invoked as a plugin and `$CLAUDE_PLUGIN_ROOT` is set, prefer that. Python
scripts resolve their own siblings via `Path(__file__).parent`, so when running
them you only need the right path to the script itself.

## allowed-tools tradeoff

SKILL.md ships this allowlist: `Bash(python3 *) Bash(npx skills find *)
Bash(wp context *) Read mcp__context-a8c__context-a8c-load-provider
mcp__context-a8c__context-a8c-execute-tool`. This covers scan/analyze/render/
history (all `python3`), the skills.sh registry (`npx skills find`),
cli-provider catalogs (`wp context`), reading marketplace JSON / SKILL.md
(`Read`), and probing `mcp-server` catalogs the scanner discovers (via the two
generic context-a8c MCP tools).

**`mcp-server` catalogs ARE probed by the live session.** A Python subprocess
can't reach MCP, so `catalog_search.py` emits any discovered `mcp-server`
catalog as a `needs_live_probe` candidate; the live session then loads its
provider and runs its `search`/`get` tools per job phrase (Step 3, MCP-server
bullet). The MCP tool names are environment-specific — whichever ones are in
`allowed-tools` and connected can be probed; the rest degrade to skip-silently
(graceful, not a break). Add other MCP tool names to `allowed-tools` to probe
additional catalogs.

`npx skills add` / `npx skills init` are **printed, not run** — the skill never
installs anything. Only `npx skills find` is in the allowlist (read-only query).

## Mandatory execution order (two gates can terminate the run)

**Fast path (default — 3 calls):**

1. **`run.py`** — wraps scan + analyze + staging + trends. Prints a slim JSON:
   `gates` (`session_count`, `catalogs`), `paths` (workdir, `scan.json`,
   `analysis.json`, staged `payload.json` + `snapshot.json`), the
   `markdown_skeleton`, `job_signals` (work mix, top bash verbs, top MCP calls,
   recurring prompts, web fetches), and `trends` (`distinct_days` + ready
   "vs last run" rows). Read `paths.analysis` only if you need a field the
   summary doesn't carry.
2. **GATE A — empty session data.** `gates.session_count == 0` → **STOP.** Tell the user there's no recent session data. Do not cluster, query catalogs, render, or append history.
3. **GATE B — no catalogs.** `gates.catalogs` empty → **emit zero catalog-backed recommendations** and tell the user to add a marketplace (`/plugin marketplace add anthropics/claude-plugins-official`) or install Node so `npx skills` works. Coaching from local signals may still run.
4. Cluster jobs (Step 2), then **one batch catalog call** (Step 3, `--jobs`),
   probe `needs_live_probe` MCP catalogs, then **`finalize.py`** (merge fill +
   render + history in one call), then print the report.

`scan.py`, `analyze.py`, `render_report.py`, `history.py` remain runnable
standalone (debugging, partial reruns); the gates are identical either way.

A job with **no real catalog match** is routed to "Worth building yourself" — never a forced/weak recommendation.

## Step 1 — scan

```bash
python3 ${CLAUDE_SKILL_DIR}/bin/scan.py --days 28 --budget compact
```

Budgets: `compact` (default — smallest output), `normal`, `full` (richest). Use
`compact` unless the user asks for more depth.

### Scan JSON fields

- **Evidence:** `session_count`, `projects`, `tool_use_top`, `mcp_calls_top`, `bash_verbs_top`, `bash_verb_samples`, `web_fetches`, `recurring_prompts`, `sampled_oneoff_prompts`, `session_index`. Every "you did X N times" claim comes from here.
- **`bash_verb_samples`** maps a verb → real command lines. Use when a top verb is ambiguous (`node`, `curl`, `python3`, `for`, `cd`); quote in the Evidence line.
- **Filters:** `installed_skills`, `installed_plugins`, `ignored_names`. Drop any search result whose `name`/`slug` matches these.
- **Catalogs:** `available_catalogs` — each entry has `name` + `type` (`marketplace` / `cli-provider` / `cli-registry` / `mcp-server`) plus a type-specific field: `marketplace_json` (local JSON index), `tool` (`wp context <provider>` or `npx skills find`), or `probe` (mcp-server instructions). The `cli-registry` (skills.sh) also carries `install_tool` / `init_tool`. Query every catalog; never hardcode one. `mcp-server` entries are *candidates* the scanner couldn't probe — probe them yourself (Step 3).
- **`coaching_signals`** — `native_tool_bypass {bash_total, bypass_calls, bypass_total, suggested_tool, native_tool_use, bypass_result_chars, bypass_results_measured, native_result_chars, native_results_measured}`; `bash_error_chars`; `destructive_cmds [{label, count, sample}]`; `raw_http_hosts {host:count}`; `sleep_calls`; `hot_repos_without_claudemd [{path, sessions}]`. The `*_chars` fields are measured tool-result sizes (tokens ≈ chars/4) feeding the 💸 savings estimate: search verbs (grep/rg/find/awk) vs Grep/Glob only — like-for-like; cat/head→Read is cost-neutral and never counted as avoidable.
- **`work_recap`** — `{top_projects:[{path, sessions, tokens, kind, branch, commits, pushes}], mix:{dev,writing,data,ops}}`. Use to describe what the user works on and to weight which recommendations surface first.
- **`outcomes`** — Anthropic's labels. `{by_facet, friction_sessions, friction_counts_sum, primary_success_top, session_type_mix, helpfulness_mix, coverage}`. **Coverage required** — every rate over `coverage.labeled`, never `coverage.total`. Raw enums in code; pretty labels only in display.
- **`completion`** — `{sessions_with_commit, sessions_with_push, lines_added, lines_removed, files_modified, prs_detected_via_gh, coverage}`. Call this **commits/pushes**, never "PRs" — `prs_detected_via_gh` is opportunistic and must always be labeled as such.
- **`tool_errors`** — `{<tool>: {ok, error}}`. Surface Bash error rate etc.
- **`memory_events`** — `{remember_invocations, memory_file_edits, sessions_with_memory}`.
- **`model_mix`** — `{by_model: {opus|fable|sonnet|haiku: {calls, in, out, cache_read, cache_write}}, automated_premium: {sessions, out_tokens, cache_read_tokens}}`. `automated_premium` counts sessions whose identical first prompt repeats ≥3× and that produced output on a premium family — the wrong-model-for-automation signal feeding the scorecard row, coaching card, and 💸 model-waste estimate.
- **`stuck_loops`** — `[{command_hash, command_summary, command, count, session, first_ts, last_ts}]` for runs of ≥3 identical Bash commands ≤2 min apart. `command` is for this run only — never into history.

### Dismissing a recommendation

```bash
python3 ${CLAUDE_SKILL_DIR}/bin/scan.py --ignore <name>     # reverse: --unignore; list: --list-ignored
```

## Step 2 — cluster signals into 3–5 recurring jobs

Identify 3–5 distinct "jobs" from bash verbs, tool-use mix, recurring prompts,
MCP calls. Write a short tag per job + the concrete evidence count. Drop any job
you can't tie to specific counts. **Weight jobs by `work_recap`** — lead with the
area where the user spends the most time/tokens; serve non-dev work too.

## Step 3 — query every available catalog

**Fast path (batch — one call for all jobs):**
`python3 ${CLAUDE_SKILL_DIR}/bin/catalog_search.py --scan <scan.json>
--jobs "<job 1 phrase, phrase>|<job 2 phrase>|..." --top 6` runs every job in
parallel. `|` separates jobs; commas separate search phrases within a job —
keep phrases whole ("git safety", not "git" + "safety": registries rank
phrases far better, and marketplace matching ANDs a phrase's words anyway).
It
and returns `{"jobs": [{job, candidates}], "needs_live_probe", "errors"}` —
**verified candidates only** (marketplace + cli-provider + skills.sh), already
deduped, trimmed to `--top`, with installed/ignored names dropped. It never
invents: skills.sh's human output is parsed with a strict registry-shape
regex, so every name/URL/install command is verbatim from the registry.
`needs_live_probe` lists the `mcp-server` catalogs only the live session can
reach — probe those yourself below. Then pick one winner per job from its
`candidates` using the ranking rules in "Combine results".

Single-job mode (`--terms "<job tag>" ...`) still works and returns the flat
`{"candidates": ...}` shape.

Otherwise, or to cover what the helper flagged, query by hand. Loop over **every**
entry in `available_catalogs`. Same matching logic, different lookup tool. Query
each with short noun-phrases from the job tag (substring matches, not full
sentences).

- **Marketplace** (`type: "marketplace"`): read `marketplace_json` (top-level `plugins: [...]`, each with `name`, `description`, metadata). Case-insensitive substring match on `name`/`description`/`keywords`. Install: `/plugin install <name>@<marketplace-name>`.
- **CLI-provider** (`type: "cli-provider"`, e.g. `tool: "wp context <provider>"`): `<tool> search query=<term> limit=10`. Results: `name`, `slug`, `type`, `description`, `repo_key`, `source_url`.
- **CLI-registry** (skills.sh, `tool: "npx skills find"`): `npx skills find <term>`. Largest catalog — query for every job. Results carry name, owner/repo, description, **install count**, **GitHub stars** — capture these for ranking. First call may be slow.
- **MCP-server** (`type: "mcp-server"`): scanner couldn't probe — only the live session has MCP. For each: (1) load the provider and enumerate its tools; (2) keep only catalog providers — a `search`+`get` pair whose `search` description mentions skill/plugin/agent/marketplace/directory/catalog; (3) `search` once per job phrase, keep only hits whose name/description fit the job (substring matches can be broad and alphabetical, not relevance-ranked), then `get` for final picks. Same result shape as cli-provider (`name`, `slug`, `type`, `description`, `repo_key`, `source_url`). If unreachable or no catalog provider, skip silently.
  - **Calls** (e.g. a discovered `context-a8c` server exposing an `ai-skills` catalog): `mcp__context-a8c__context-a8c-load-provider` `{provider:"ai-skills"}`, then `mcp__context-a8c__context-a8c-execute-tool` `{provider:"ai-skills", subtool:"search", subtool_args:{query:"<job phrase>", limit:8}}`, and `subtool:"get"` `{slug, repo_key}` for picks. Other servers expose their own provider/tool names — read them from the load-provider result.

### Combine results across catalogs

Per job, merge candidates:
- **Dedupe by name.** Prefer skills.sh (install counts + stars) > cli-provider (richer descriptions, real SKILL.md via `get`) > marketplace.
- **Pick ONE winner per job:** (1) drop anything in `installed_skills`/`installed_plugins`/`ignored_names`; (2) prefer entries that explicitly describe the job; (3) tie-break on hard popularity when reported — install count (1K+ = solid; <100 = caution, say so), GitHub stars (<100 = skepticism), official/blessed sources outrank unknown authors; (4) when no numbers exist (local marketplaces, cli-providers), fall back to blessed source > richer description > newer. Don't penalize an entry for lacking install counts.

If nothing clearly matches → "Worth building yourself". **Use the right label** from each entry's `type` field: "Recommended plugin:" / "Recommended skill:" / "Recommended agent:". Never call a plugin a skill.

## Step 3.5 — fetch full details for final picks

- **CLI-provider hit:** `<tool> get slug=<slug> repo_key=<repo_key>` → full SKILL.md.
- **skills.sh hit:** find result carries name/installs (description/stars when the CLI emits JSON; text-parsed hits have an empty description — describe the pick from its name + registry data, or read `https://www.skills.sh/<owner>/<repo>/<skill>` for the full SKILL.md; never invent a description).
- **Marketplace hit:** read the entry from `marketplace_json`. Capture `homepage` as `source_url` (fall back to `source.url`). If cached at `~/.claude/plugins/marketplaces/<mp>/<plugin>/`, read its README/SKILL.md.

Install command rules:
- **skills.sh:** `npx skills add <owner/repo>@<skill> -g -y` (`-g` user-level, `-y` skip confirm). Preferred for any skills.sh result.
- **Plugin in a marketplace:** `/plugin install <name>@<marketplace>`. If marketplace not added, prepend `/plugin marketplace add <source>` (from `known_marketplaces.json` or the entry's `repo_key`).
- **Standalone skill (no skills.sh entry):** clone repo + copy skill dir into `~/.claude/skills/<name>/`.

If `get` fails for a cli-provider pick, fall back to the search description and flag `Install: see source_url` (unverified).

## Step 6 — finalize (merge + render + history, one call)

Write `fill.json` with only what the live session adds:

```json
{"recommendations": [{"rank": 1, "confidence": "high", "type": "skill",
                      "name": "...", "job": "...", "evidence": "...",
                      "description": "...", "install": ["..."],
                      "source_url": "..."}],
 "gaps": [{"tag": "...", "note": "...", "init": "npx skills init ..."}]}
```

Then:

```bash
python3 ${CLAUDE_SKILL_DIR}/bin/finalize.py --fill fill.json
```

It merges the fill into the staged `payload.json` (from `run.py`'s workdir;
override with `--workdir`), renders the HTML, appends the history snapshot, and
prints `{"url","path","history"}`. The `url` becomes the **first line** of the
markdown report. If rendering fails, skip the link silently — the markdown
report stands alone. The HTML is fully self-contained (inline CSS + SVG, no
network).

Manual fallback: `render_report.py <payload.json>` + `history.py append
<snapshot.json>` still work for partial reruns.

Payload schema (authoritative version in `bin/render_report.py` docstring):
- `meta`: `{days, sessions, projects, date, catalogs}` (date = today, ISO).
- `verdict`: `{name, summary, evidence_chips:[...], next_phrase}` (mirrors `primary_action.phrase`).
- `archetype`: `{title, tagline, why, strength, watch_out, next_ritual}`.
- `primary_action`: `{title, phrase, why, source}`.
- `recommendations`: `[{rank, confidence:"high"|"med"|"low", type, name, job, evidence, description, install:[cmds], source_url}]`.
- `gaps`: `[{tag, note, init}]`.
- `coaching`: `[{title, evidence, costs, better}]` (renderer labels them What we saw / Why it matters / Try this).
- `work_recap`: pass straight from scan JSON.
- `scorecard`: `[{label, value, verdict, note, explain, history_key?, current_number?}]` — `verdict` ∈ `good`/`watch`/`needs_action`/`no_data` (legacy `warn`→`watch`, `bad`→`needs_action` accepted). Each row expandable.
- `charts`: `{tool_use_top, bash_verbs_top}` straight from scan JSON.
- **Raw scan pass-through (REQUIRED for gamify):** copy `coaching_signals`, `outcomes`, `memory_events`, `recurring_prompts`, `tool_errors`, `completion`, `stuck_loops`, `installed_skills` as-is. Without them gamify renders "No active quest this run".

## Step 7 — append numeric history snapshot

After rendering, write a tiny numeric-only snapshot. **Numbers only — no command
strings, paths, or session IDs.**

```bash
python3 ${CLAUDE_SKILL_DIR}/bin/history.py append < /tmp/skills-daimon-snapshot.json
```

`(date, window_days)` is the dedupe key — same-day reruns overwrite, so the
sparkline never shows fake movement. Snapshot shape:

```json
{
  "date": "<YYYY-MM-DD>", "window_days": 28, "sessions": <N>, "labeled": <outcomes.coverage.labeled>,
  "scorecard": {"outcome_finished_pct": <int>, "bash_error_pct": <float>, "memory_rate_pct": <int>,
    "search_shell_pct": <int>, "risky_git_count": <int>, "claudemd_missing": <int>, "unsaved_prompts": <int>},
  "archetype": "<title>", "work_mix": {"dev": <pct>, "writing": <pct>, "data": <pct>, "ops": <pct>},
  "game": { /* numeric-only gamify snapshot */ }
}
```

`scorecard` keys must match the `history_key` on the corresponding scorecard rows.

### The Daimon Grove block (`game`, numeric only)

Append only the numeric snapshot from `gamify.numeric_game_history_snapshot(state)`
— never raw quest titles, badge names, or evidence strings. The history file
enforces this (non-numeric values dropped); follow it in your snapshot too.

```bash
python3 - <<'PY'
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path("${CLAUDE_SKILL_DIR}") / "bin"))
import gamify
analysis = json.load(open("/tmp/sd-payload.json"))
state = gamify.build_game_state(analysis, history_snapshots=None, today="<YYYY-MM-DD>", window_days=28)
snap = {"date": "<YYYY-MM-DD>", "window_days": 28, "sessions": ..., "labeled": ...,
        "scorecard": {...}, "archetype": "...", "work_mix": {...},
        "game": gamify.numeric_game_history_snapshot(state)}
json.dump(snap, sys.stdout)
PY
```

XP is awarded only when the *next* run verifies an improvement vs the prior
distinct day; same-day reruns don't double-award; weak signals never subtract XP.
