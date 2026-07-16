# Claude Code skills & agents

Two products live here:

1. **[daimon](daimon/)**: reads your past sessions and shows you what to do to improve your AI usage — plus a companion skill that saves what you learn so it compounds.
2. **[orchestration](orchestration/)**: a small AI team for coding work. Instead of one AI doing everything in one go, you get a team with clear jobs: one plans, one reads the code, one writes the change, one runs the tests, one double-checks everything. You drive it with three commands: `/feature`, `/bug`, `/review`.

## Part 1: the daimon loop ([daimon](daimon/))

### [skills-daimon](daimon/skills-daimon/) — "how am I actually using this thing?"

Reads your recent Claude Code sessions (all on your own computer — nothing is uploaded anywhere) and writes you a personal report:

- what you've been working on lately
- habits that are costing you time, with real counts — like searching files the slow way, or typing the same long prompt over and over
- skills and plugins that would actually help *you*, picked from catalogs that really exist (it never invents one)
- a fun "archetype" card you can share, and progress tracking if you run it regularly

Run it with `/skills-daimon`. Also works if you use OpenAI's Codex instead of Claude Code.

### [learnings-keeper](daimon/learnings-keeper/) — "remember what we figured out"

At the end of a session where you cracked something — a bug, a decision, a gotcha — this saves a short note about it (into your Obsidian vault if you have one, or a plain folder if you don't). Weeks later, when the same problem shows up again, it finds that note and brings it back. Nothing is saved without showing you the note first.

The two work as a loop: skills-daimon *diagnoses* (you don't save what you learn); learnings-keeper *treats it* — and daimon's coaching resurfaces old notes when a similar friction comes back.

## Part 2: the AI team ([orchestration](orchestration/))

Normally you ask an AI assistant to build something and it just... starts typing code. This works differently. When you type `/feature add dark mode`, here's what happens:

1. A **scout** reads your codebase first and reports back: which files matter, how things connect, what could break.
2. The **lead** makes a plan and decides exactly which files may be touched.
3. A **builder** makes the smallest change that does the job — no surprise rewrites of things you didn't ask about.
4. A **tester** runs the tests and reports what passed and what didn't.
5. A **reviewer** who wasn't involved in any of the above checks the work for real mistakes. For extra safety, a second reviewer from a different company (OpenAI's Codex) checks it too — two different AIs catch more than one, because they don't make the same mistakes.
6. If the reviewers find real problems, the builder fixes them and the tests run again.

The three commands:

```
/feature add dark mode to the settings page     → build something new
/bug the app logs me out after every refresh    → find and fix the real cause
/review focus on security                        → just check my current changes, touch nothing
```

Each helper's job never changes, but the AI model doing that job can be swapped when a better one comes out — there's even a little scoring sheet ([EVALS.md](orchestration/EVALS.md)) to test whether a new model is actually better at a job before giving it the seat.

## Install

**The daimon skills**, via [skills.sh](https://www.skills.sh):

```bash
npx skills@latest add escapemanuele/skills
```

Or by hand:

```bash
git clone https://github.com/escapemanuele/skills.git /tmp/eb-skills
cp -r /tmp/eb-skills/daimon/skills-daimon ~/.claude/skills/skills-daimon
cp -r /tmp/eb-skills/daimon/learnings-keeper ~/.claude/skills/learnings-keeper
```

Then type `/skills-daimon` in Claude Code.

**The AI team:**

```bash
git clone https://github.com/escapemanuele/skills.git
./skills/orchestration/install.sh
```

Then use `/feature`, `/bug`, `/review` in any repo on your machine.

## Want the details?

Each folder has its own README that goes deeper — how it works inside, what it writes to disk, and the design decisions:
[skills-daimon](daimon/skills-daimon/README.md) ·
[learnings-keeper](daimon/learnings-keeper/README.md) ·
[orchestration](orchestration/README.md)

## License

MIT
