# Claude Code skills & agents

Skills, subagents, and multi-agent workflows for [Claude Code](https://claude.com/claude-code). Two independent things live here:

1. **The skills-daimon plugin** — three skills that work as a loop: a report that reads your own sessions and finds what to improve, plus two companions that fix the most common findings.
2. **An agent orchestration system** — role-based multi-agent workflows (`/feature`, `/bug`, `/review`) built on four narrowly-scoped subagents, with swappable model bindings and an adversarial cross-family Codex review pass.

## What's inside

| Folder | What it is | Docs |
|---|---|---|
| [`skills-daimon/`](skills-daimon/) | Evidence-based workflow report: reads your recent Claude Code (or Codex) sessions, scores your habits with hard counts, recommends real skills/plugins, crowns you with an archetype, tracks trends. No LLM in the scan; nothing leaves your machine. | [README](skills-daimon/README.md) |
| [`prompt-to-command/`](prompt-to-command/) | Turns a prompt you keep retyping into a saved slash command (`/journal evening` instead of pasting a paragraph). skills-daimon *finds* the repeated prompt; this one *fixes* it. | [README](prompt-to-command/README.md) |
| [`learnings-keeper/`](learnings-keeper/) | Saves what you figured out in a session as plain markdown (Obsidian vault or a default folder), then resurfaces it when a similar problem comes back. Preview-before-save, secret redaction at every disk boundary. | [README](learnings-keeper/README.md) |
| [`orchestration/`](orchestration/) | Role-based multi-agent system: `/feature`, `/bug`, `/review` lead workflows orchestrating a read-only explorer, bounded implementer, independent reviewer, and test runner in a star topology. Roles are stable; model bindings swap via per-role evals. `/review` adds a parallel adversarial pass from OpenAI Codex. | [README](orchestration/README.md) |

## Install

**The skills-daimon plugin** (first three folders), via [skills.sh](https://www.skills.sh):

```bash
npx skills@latest add escapemanuele/skills
```

Or manually:

```bash
git clone https://github.com/escapemanuele/skills.git /tmp/eb-skills
cp -r /tmp/eb-skills/skills-daimon ~/.claude/skills/skills-daimon
```

Then run `/skills-daimon`. It also works with OpenAI Codex — see the [skills-daimon README](skills-daimon/README.md#using-codex-instead-of-claude-code).

**The orchestration system** (separate from the plugin, on purpose):

```bash
git clone https://github.com/escapemanuele/skills.git
./skills/orchestration/install.sh
```

Then use `/feature <description>`, `/bug <symptom>`, `/review [focus]` in any repo.

## Layout

```
skills/                              # repo (escapemanuele/skills)
├── .claude-plugin/plugin.json      # plugin "skills-daimon" + its three skill paths
├── skills-daimon/                  # the report (SKILL.md, bin/, references/, tests/)
├── prompt-to-command/              # repeated prompt → slash command
├── learnings-keeper/               # session learnings → markdown notes + lookup
└── orchestration/                  # agents/, skills/{feature,bug,review}/, docs, install.sh
```

Each folder's README goes deeper: what it does, how it works, and design decisions.

## License

MIT
