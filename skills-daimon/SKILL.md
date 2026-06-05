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
- Every coaching claim must cite a hard count from the scan.
- Do not install anything. Print install commands only.
- If no sessions exist, say there is no recent data.
- If a catalog is unreachable, report it as unavailable.

## Pipeline

1. **Scan** — `python3 ${CLAUDE_SKILL_DIR}/bin/scan.py --days 28 --budget compact`
   - **GATE A:** if `session_count == 0` → STOP, tell the user there's no recent data. End.
   - **GATE B:** if `available_catalogs` is empty → emit zero catalog-backed recs (coaching may still run).
2. **Analyze** — `python3 ${CLAUDE_SKILL_DIR}/bin/analyze.py <scan.json>` computes the verdict, archetype, primary action, scorecard, coaching, history snapshot, and a markdown skeleton deterministically (all rates carry denominators; outcome rates use `outcomes.coverage.labeled`). Its `recommendations`/`gaps` are empty by design — you fill those from live catalog hits in step 3. See `references/analysis.md` to verify or reason by hand.
3. **Recommend** — cluster 3–5 jobs, query *every* `available_catalogs` entry, pick one catalog-backed winner per job. Unmatched jobs → "Worth building yourself".
4. **Render HTML** — `python3 ${CLAUDE_SKILL_DIR}/bin/render_report.py <payload.json>`; link the printed `url` at the top of the report.
5. **Append history** — `python3 ${CLAUDE_SKILL_DIR}/bin/history.py append < <snapshot.json>` (numeric-only).
6. **Print** the markdown report with the visual report URL.

## References (read only when needed)

- `${CLAUDE_SKILL_DIR}/references/charter.md` — promise, tone, banned phrases, non-negotiables, failure modes.
- `${CLAUDE_SKILL_DIR}/references/analysis.md` — verdict ladder, primary-action ladder, scorecard thresholds, coaching rules, archetypes, gaps. (`analyze.py` implements these; read when reasoning by hand or verifying.)
- `${CLAUDE_SKILL_DIR}/references/report-format.md` — markdown template, link rules, confidence dots, tone.
- `${CLAUDE_SKILL_DIR}/references/pipeline.md` — scan JSON fields, the two gates, catalog matching (Steps 3 & 3.5), render payload schema, history/gamify snapshot, dismiss command, allowed-tools/MCP tradeoff.
