# skills

A collection of Claude Code skills by [@escapemanuele](https://github.com/escapemanuele).

The headline skill here is **skill-fit** — the evidence-based way to figure out how to get more out of Claude Code.

## Why skill-fit

Most "what should I install?" advice is guesswork. **skill-fit is different: it reads your own recent Claude Code sessions, finds the jobs you keep doing by hand, and recommends real skills/plugins that match — drawn only from catalogs actually reachable on your machine. It never invents a skill.**

If you want to improve your Claude usage, this is the right starting point: it grounds every recommendation in what you actually do, not in generic best practices.

## What skill-fit does

- **Scans** your recent Claude Code sessions (last 14 days by default).
- **Clusters** the signals — bash verbs, MCP calls, recurring prompts — into 3–5 recurring jobs.
- **Recommends** matching skills/plugins from every catalog it can reach (local plugin marketplaces and CLI-provider catalogs).
- **Filters** out anything you already have installed or have dismissed.
- **Never invents** a skill — every recommendation comes from a real catalog entry, or it's honestly flagged as a gap.

The scan itself is deterministic (no LLM): `skill-fit/bin/scan.py` produces hard counts, and Claude reasons over that JSON.

## Install

```bash
git clone https://github.com/escapemanuele/skills.git /tmp/eb-skills
cp -r /tmp/eb-skills/skill-fit ~/.claude/skills/skill-fit
```

Then ask Claude:

> what skills should I install?

Other trigger phrases:

- "find skills that match my work"
- "what could I do better with Claude?"

## Repo layout

This repo is also a Claude Code plugin (`.claude-plugin/plugin.json`). Each skill is a folder containing a `SKILL.md`:

```
skills/
├── .claude-plugin/plugin.json   # registers the plugin + its skills
└── skill-fit/
    ├── SKILL.md                 # instructions Claude follows
    └── bin/scan.py              # deterministic session scanner
```

## License

TBD
