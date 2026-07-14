# skills-daimon

A Claude Code plugin, a *daimon* (the guiding spirit Socrates listened to) for your Claude Code work: it watches how you actually work and helps you do it better.

Most "what should I install?" advice is guesswork. skills-daimon doesn't guess. It reads your own recent Claude Code sessions, finds the jobs you keep doing by hand, and points you at real skills and plugins that match, drawn only from catalogs that actually exist on your machine. It never makes one up.

If you want to get more out of Claude Code, start here: every suggestion is grounded in what you really do, not in generic best practices. And nothing leaves your machine.

## What it does

Run `/skills-daimon` and you get a local report that:

- **Recaps** what you've been working on: top projects, plus the dev / writing / data / ops mix.
- **Scores your workflow** where there's a clear better way: shell search vs. built-in tools, risky git, hand-rolled API calls, repeated prompts you never saved. Each signal cites a hard count, never a vibe.
- **Recommends real skills/plugins** from every catalog it can reach (local marketplaces, a live `npx skills find`, `ai-skills`, and any MCP-server catalogs it discovers), weighted toward what you actually work on. If nothing real matches a pattern, it says so instead of padding.
- **Coaches** a couple of small habit changes from the same evidence (native tools over raw shell, safer git, CLAUDE.md, saved commands), and hands you off to the sibling skills below when they fit.
- **Crowns you** with a playful archetype (e.g. *The Builder-Scribe*) based on how you work, with a one-tap shareable "wrapped" card you can post.
- **Grows the Daimon Grove**, a conservative gamification layer (XP, levels, badges, quests across six tracks) that only rewards *verified* habit improvements between runs, never raw usage.
- **Trends** your numbers over time once you have a few runs banked.
- **Trims token usage** with concrete tips drawn from your own session shape.

The report opens as a self-contained HTML page (simplified view first, with an Advanced toggle), and prints a clean Markdown version in the chat.

## How it works

The scan is deterministic, no LLM in the loop. `scan.py` produces hard counts, `analyze.py` turns them into a verdict/archetype/scorecard skeleton, and Claude only reasons over that small JSON. The whole pipeline is 3 calls:

1. `run.py`: scan + analyze + stage a workdir, print a slim summary.
2. `catalog_search.py` (+ live MCP-catalog probing): cluster 3 to 5 jobs and find real matches.
3. `finalize.py`: merge the picks, render the HTML, append a numeric-only history snapshot.

Privacy is the whole point: every disk write goes through a shared redactor, history stays numeric-only, stuck-loop notes are hashed, and the report never leaves your machine.

## Install

Via [skills.sh](https://www.skills.sh):

```bash
npx skills@latest add escapemanuele/skills
```

Or manually:

```bash
git clone https://github.com/escapemanuele/skills.git /tmp/eb-skills
cp -r /tmp/eb-skills/skills-daimon ~/.claude/skills/skills-daimon
```

Then run it:

```
/skills-daimon
```

It takes optional flags: `/skills-daimon --days 28 --full` (window size, plus `--compact`/`--normal`/`--full` detail).

## Using Codex instead of Claude Code

skills-daimon also reads OpenAI Codex sessions (`~/.codex/sessions`). Install it as a Codex skill:

```bash
git clone https://github.com/escapemanuele/skills.git /tmp/eb-skills
cd /tmp/eb-skills/skills-daimon && ./install-codex.sh
```

This copies the scripts into `~/.codex/skills/skills-daimon/` with a Codex-native `SKILL.md`. Then run `skills-daimon` in Codex, or directly:

```bash
python3 ~/.codex/skills/skills-daimon/bin/run.py --days 28 --source codex
```

The Codex-installed skill forces `--source codex`, so mixed machines with both
Claude Code and Codex history still report Codex usage. Reports and history land
under `~/.codex/skills/skills-daimon/` (override with `SKILLS_DAIMON_HOME`).
Codex has no per-session outcome labels, so the "finished %" rows read no-data —
expected. Only `python3` is required; `npx` unlocks the live skills.sh registry
query.

## The three skills

This repo is the `skills-daimon` plugin (`.claude-plugin/plugin.json`). It ships three skills that work as a loop: the report finds the problem, the siblings fix it.

- **skills-daimon**: the report itself: recap, recommendations, workflow signals, coaching, archetype, and the Daimon Grove.
- **prompt-to-command**: turn a prompt you keep retyping into a saved slash command. skills-daimon *finds* the repeated prompt; this one *fixes* it.
- **learnings-keeper**: save what you figured out in a session as plain markdown (your Obsidian vault or a default folder), then surface it again when a similar problem comes back. skills-daimon *flags* low memory usage; this one *captures and resurfaces* the lesson.

## Layout

```
skills/                              # repo (escapemanuele/skills)
├── .claude-plugin/plugin.json       # plugin "skills-daimon" + skill paths
├── skills-daimon/
│   ├── SKILL.md
│   ├── EVALUATION.yaml
│   ├── bin/
│   │   ├── run.py                   # one-shot orchestrator (scan + analyze + stage)
│   │   ├── scan.py                  # deterministic session scanner
│   │   ├── analyze.py               # verdict / archetype / scorecard / coaching
│   │   ├── catalog_search.py        # batched, registry-verbatim catalog matching
│   │   ├── finalize.py              # merge picks + render HTML + append history
│   │   ├── gamify.py                # the Daimon Grove (XP, levels, badges, quests)
│   │   ├── render_report.py         # self-contained HTML report + shareable card
│   │   ├── render_grove_levels.py   # grove level-showcase renderer
│   │   ├── history.py               # numeric-only trends
│   │   └── redact.py                # shared secret scrubber
│   ├── references/                  # charter, analysis spec, pipeline, report format
│   └── tests/
├── prompt-to-command/
│   └── SKILL.md
└── learnings-keeper/
    ├── SKILL.md
    └── bin/
        ├── store.py                 # store config + Obsidian autodetect
        ├── extract.py               # candidate-notes extractor
        ├── save.py                  # write a learning note (redacted)
        ├── lookup.py                # search past learnings
        └── redact.py                # vendored secret scrubber
```

## Also in this repo: agent orchestration system

[`orchestration/`](orchestration/) is a separate, self-contained system (not part of the skills-daimon plugin): role-based multi-agent workflows for Claude Code — `/feature`, `/bug`, `/review` slash skills orchestrating four narrowly-scoped subagents (read-only explorer, bounded implementer, independent reviewer, test runner) in a star topology, plus an adversarial cross-family Codex review pass. Roles are stable; model bindings are swappable via evals (`MODEL-POLICY.md`, `EVALS.md`).

Install:

```bash
./orchestration/install.sh
```

See [`orchestration/README.md`](orchestration/README.md).

## License

MIT
