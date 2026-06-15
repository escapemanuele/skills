---
name: skills-daimon
description: Analyze recent OpenAI Codex (or Claude Code) sessions and produce a local, evidence-backed report with workflow coaching, catalog-backed skill recommendations, and one next action. Use when the user asks "what skills should I install", "how can I get more out of Codex", "analyze my sessions", or runs skills-daimon.
metadata:
  short-description: Local report on your recent Codex sessions with skill recommendations and coaching
---

# Skills Daimon (Codex)

Run a local, privacy-first report over recent OpenAI Codex (or Claude Code)
sessions. Spot recurring jobs, recommend matching skills from real catalogs
only, coach better habits — all grounded in hard counts. Local-only; nothing
leaves the machine.

The bundled Python scripts live in this skill's `bin/` directory. Reference them
at `${CODEX_HOME:-$HOME/.codex}/skills/skills-daimon/bin/` (the canonical install
path). They need only `python3` (stdlib) and, for the live registry query,
`npx`. Map a `--compact|--normal|--full` request to `--budget` (default
`compact`).

## Rules

- Never invent a skill, source URL, or install command. Every recommendation
  comes from a real catalog hit.
- Aim for **at least 3** recommendations: widen job terms and re-query before
  settling. If fewer than 3 real hits exist, show what's real and say so.
- Every coaching claim must cite a hard count from the scan.
- Do not install anything. Print install commands only.
- If no sessions exist, say there is no recent data.

## Pipeline (3 calls)

Let `SD="${CODEX_HOME:-$HOME/.codex}/skills/skills-daimon/bin"`.

1. **Run** — `python3 "$SD/run.py" --days 28 --budget compact`
   Scans, analyzes, stages `payload.json`/`snapshot.json` in a workdir, and
   prints a slim summary — `source`, gates, file paths, the markdown skeleton
   (verdict, archetype, scorecard, coaching all precomputed; rates carry
   denominators), `job_signals` for clustering, and ready-made `trends` rows.
   - **Source:** defaults to `--source auto`, which scans Codex
     (`~/.codex/sessions`) when Claude Code sessions aren't present. On a
     Codex-only machine you can omit the flag. Codex has no per-session outcome
     labels and no built-in Grep/Glob/Read, so the "finished %" scorecard rows
     and the shell-vs-built-in signal read no-data — expected, not a bug.
   - **GATE A:** if `gates.session_count == 0` → STOP, tell the user there's no recent data.
   - **GATE B:** if `gates.catalogs` is empty → emit zero catalog-backed recs (coaching may still run).
2. **Recommend** — cluster 3–5 jobs from `job_signals` + the skeleton, then **one
   batch call**:
   `python3 "$SD/catalog_search.py" --scan <paths.scan> --jobs "git workflow, commit|code review, refactor|web research" --top 6`
   `|` separates jobs; **commas separate search phrases within a job — keep
   phrases whole** ("git workflow" ranks far better than "git" + "workflow").
   The scan's `source` tag is honored automatically: Codex-incompatible installs
   (Claude `/plugin install …`) are flagged `portable:false` with a source
   pointer — surface those as "open the source", and prefer the portable
   `npx skills add …` installs that work in Codex. Jobs run in parallel and the
   live registry (`npx skills find`) is queried inside the call.
   **Then probe every `needs_live_probe` mcp-server catalog yourself** with your
   own MCP tools: load the provider, find its `search`+`get` pair, `search` once
   per job phrase, keep only fitting hits, and `get` for final picks. Pick one
   catalog-backed winner per job; unmatched jobs → "Worth building yourself".
3. **Finalize** — write `fill.json` (`{"recommendations": [...], "gaps": [...]}`,
   schema in `references/pipeline.md`), then
   `python3 "$SD/finalize.py" --fill fill.json --workdir <paths.workdir>`
   Merges the fill into the payload, renders the self-contained HTML report, and
   appends the numeric-only history snapshot. It prints the report `url`.
4. **Print** the markdown report as the final message. **First line = the visual
   report `file://` link**, then the report per `references/report-format.md`.
   Nothing else — no pipeline narration, no raw JSON.

## References (read only when needed)

Under `${CODEX_HOME:-$HOME/.codex}/skills/skills-daimon/references/`:

- `charter.md` — promise, tone, banned phrases, non-negotiables.
- `analysis.md` — verdict ladder, scorecard thresholds, coaching rules, archetypes.
- `report-format.md` — markdown template, link rules, confidence dots, tone.
- `pipeline.md` — scan JSON fields, the two gates, catalog matching, payload schema.
