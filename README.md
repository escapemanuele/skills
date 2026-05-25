# skills-daimon

A Claude Code plugin by [@escapemanuele](https://github.com/escapemanuele) — a *daimon* (the guiding spirit of Socrates) for your Claude Code work: it watches how you actually work and helps you do it better.

**skills-daimon** is the evidence-based way to figure out how to get more out of Claude Code.

## Why skills-daimon

Most "what should I install?" advice is guesswork. **skills-daimon is different: it reads your own recent Claude Code sessions, finds the jobs you keep doing by hand, and recommends real skills/plugins that match — drawn only from catalogs actually reachable on your machine. It never invents a skill.**

If you want to improve your Claude usage, this is the right starting point: it grounds every recommendation in what you actually do, not in generic best practices.

## What skills-daimon does

- **Scans** your recent Claude Code sessions (last 4 weeks by default).
- **Recaps** what you've been working on — top projects + a dev/writing/data/ops mix.
- **Scores** how you work (a Health check: shell-vs-built-in search, risky git, hand-rolled API calls, unsaved repeated prompts) — only where there's a clear better way.
- **Recommends** matching skills/plugins from every catalog it can reach (local marketplaces, skills.sh, and org/MCP catalogs), weighted by what you actually work on.
- **Coaches** better habits from the same evidence (native tools over raw shell, safer git, CLAUDE.md, saved commands).
- **Crowns** you with a playful archetype (e.g. *The Builder-Scribe*) based on how you work.
- **Never invents** a skill — every recommendation comes from a real catalog entry, or it's honestly flagged as a gap.

The scan is deterministic (no LLM): `skills-daimon/bin/scan.py` produces hard counts, and Claude reasons over that JSON. A self-contained HTML report is rendered alongside. Nothing leaves your machine.

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

Then ask Claude:

> what skills should I install?

Other trigger phrases:

- "find skills that match my work"
- "what could I do better with Claude?"

## Skills

This repo is the `skills-daimon` Claude Code plugin (`.claude-plugin/plugin.json`). Each skill is a folder with a `SKILL.md`:

- **skills-daimon** — the usage report: recap, recommendations, health check, coaching, archetype.
- **prompt-to-command** — turn a prompt you keep retyping into a saved slash command. Pairs with the report: skills-daimon *finds* the repeated prompt; this one *fixes* it.
- **learnings-keeper** — save what you figured out in a session as plain markdown (Obsidian vault or default folder), and look it up later when a similar problem comes around. Pairs with the report: skills-daimon *flags* low memory usage; this one *captures and resurfaces* lessons across sessions.

```
skills/                          # repo (escapemanuele/skills)
├── .claude-plugin/plugin.json   # plugin "skills-daimon" + its skill paths
├── skills-daimon/
│   ├── SKILL.md
│   └── bin/
│       ├── scan.py              # deterministic session scanner
│       ├── render_report.py     # self-contained HTML report renderer
│       ├── history.py           # trends (counts only, no PII)
│       └── redact.py            # shared secret scrubber
├── prompt-to-command/
│   └── SKILL.md
└── learnings-keeper/
    ├── SKILL.md
    └── bin/
        ├── store.py             # store config + Obsidian autodetect
        ├── extract.py           # candidate-notes extractor
        ├── save.py              # write a learning note (redacted)
        ├── lookup.py            # search past learnings
        └── redact.py            # vendored secret scrubber
```

## License

MIT
