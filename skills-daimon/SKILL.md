---
name: skills-daimon
description: Analyze recent Claude Code sessions and produce a local, evidence-backed report with workflow coaching, catalog-backed skill/plugin recommendations, and one next action. Invoke directly with /skills-daimon.
disable-model-invocation: true
argument-hint: "[--days 28] [--compact|--normal|--full]"
allowed-tools: Bash(python3 *) Bash(npx skills find *) Bash(wp context *) Read
---

# Skills Daimon

Run a local, privacy-first report over recent Claude Code sessions. Spot recurring
jobs, recommend matching skills/plugins from real catalogs only, coach better
habits — all grounded in hard counts. Local-only; nothing leaves the machine.

Bundled files live under `${CLAUDE_SKILL_DIR}` (prefer `$CLAUDE_PLUGIN_ROOT` if
set). Map the user's `--compact|--normal|--full` flag to scan's `--budget`
(default `compact`).

## Rules

- Never invent a skill, plugin, agent, source URL, or install command.
- Every recommendation must come from a catalog hit.
- Aim for **at least 3** recommendations: widen job terms and re-query every source (incl. live `npx skills find` + `ai-skills`) before settling. If <3 real hits genuinely exist, show what's real and say so — never pad.
- Every coaching claim must cite a hard count from the scan.
- Do not install anything. Print install commands only.
- If no sessions exist, say there is no recent data.
- If a catalog is unreachable, report it as unavailable.

## Pipeline (3 calls)

1. **Run** — `python3 ${CLAUDE_SKILL_DIR}/bin/run.py --days 28 --budget compact`
   One call: scans, analyzes, stages `payload.json`/`snapshot.json` in a workdir,
   and prints a slim summary — gates, file paths, the markdown skeleton (verdict,
   archetype, scorecard, coaching all precomputed; rates carry denominators),
   `job_signals` for clustering, and ready-made `trends` rows.
   - **GATE A:** if `gates.session_count == 0` → STOP, tell the user there's no recent data. End.
   - **GATE B:** if `gates.catalogs` is empty → emit zero catalog-backed recs (coaching may still run).
2. **Recommend** — cluster 3–5 jobs from `job_signals` + the skeleton, then **one
   batch call**:
   `python3 ${CLAUDE_SKILL_DIR}/bin/catalog_search.py --scan <paths.scan> --jobs "git safety, commit push|web research, content extraction|..." --top 6`
   `|` separates jobs; **commas separate search phrases within a job — keep
   phrases whole** (registries rank "git safety" far better than "git" +
   "safety"). Jobs run in parallel; live `npx skills find` is queried inside
   it (strict registry-verbatim parsing — nothing invented). Probe any `needs_live_probe`
   mcp-server catalogs yourself. Pick one catalog-backed winner per job;
   unmatched jobs → "Worth building yourself".
3. **Finalize** — write `fill.json` (`{"recommendations": [...], "gaps": [...]}`,
   schema in `references/pipeline.md`), then
   `python3 ${CLAUDE_SKILL_DIR}/bin/finalize.py --fill fill.json`
   One call merges the fill into the payload, renders the HTML (opens in a
   **simplified view** with an advanced toggle), and appends the history
   snapshot. It prints the report `url`.
4. **Print** the markdown report as the final message. **First line = the visual
   report `file://` link**, then the report per `references/report-format.md`.
   Nothing else in that message — no pipeline narration, no raw JSON, no trailing
   notes after Trends.

(The old single-step scripts — `scan.py`, `analyze.py`, `render_report.py`,
`history.py` — still work standalone for debugging; see `references/pipeline.md`.)

## References (read only when needed)

- `${CLAUDE_SKILL_DIR}/references/charter.md` — promise, tone, banned phrases, non-negotiables, failure modes.
- `${CLAUDE_SKILL_DIR}/references/analysis.md` — verdict ladder, primary-action ladder, scorecard thresholds, coaching rules, archetypes, gaps. (`analyze.py` implements these; read when reasoning by hand or verifying.)
- `${CLAUDE_SKILL_DIR}/references/report-format.md` — markdown template, link rules, confidence dots, tone.
- `${CLAUDE_SKILL_DIR}/references/pipeline.md` — scan JSON fields, the two gates, catalog matching (Steps 3 & 3.5), render payload schema, history/gamify snapshot, dismiss command, allowed-tools/MCP tradeoff.
