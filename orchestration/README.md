# Agent Orchestration System

Role-based multi-agent workflows for Claude Code. Workflows and role definitions are stable; the models bound to each role are replaceable (see `MODEL-POLICY.md`).

**Installed at user level** (`~/.claude/agents/`, `~/.claude/skills/`, `~/.claude/orchestration/`), so `/feature`, `/bug`, `/review` and the four role agents are available in **every folder and repo on this machine** — not just one project. A project can still override any piece by shipping a same-named agent/skill in its own `.claude/` (project level shadows user level).

## Install / update

This repo folder is the source of truth. Edit here, then sync into `~/.claude`:

```bash
git clone https://github.com/escapemanuele/skills.git
./skills/orchestration/install.sh        # or re-run after any edit / git pull
```

The script copies the four agents into `~/.claude/agents/`, the three skills into `~/.claude/skills/`, and these docs into `~/.claude/orchestration/`. Set `CLAUDE_DIR` to install somewhere else. Avoid editing the installed copies directly — changes there get overwritten by the next install and never make it back to the repo.

## Mental model

```
User
  ↓
/feature or /bug or /review
  ↓
Lead (runs in the main conversation, strongest reasoning model)
  ├── Explorer            read-only repo investigation
  ├── Implementer         bounded code changes
  ├── Test runner         targeted verification
  ├── Reviewer            independent read-only review (Claude family)
  └── Codex reviewer      adversarial cross-family review (external GPT/Codex, in /review)
```

**Star topology.** Every subagent reports back to the lead; the lead synthesizes and decides. Subagents never delegate to other subagents — a deep chain loses context at every hop and nobody owns the final decision. Subagents return concise structured reports, not raw repository dumps, so the lead's context stays clean.

## Components

| Piece | File | What it is |
|---|---|---|
| Lead | `~/.claude/skills/{feature,bug,review}/SKILL.md` | Workflow prompts that run in the main conversation with the lead model binding |
| Explorer | `~/.claude/agents/code-explorer.md` | Read-only (Read/Grep/Glob only) |
| Implementer | `~/.claude/agents/implementer.md` | Edits within an explicit boundary |
| Test runner | `~/.claude/agents/test-runner.md` | Runs verification, never edits |
| Reviewer | `~/.claude/agents/reviewer.md` | Read-only tools + Bash for `git diff`/`git show` |
| Codex reviewer | `codex` plugin (`codex:codex-rescue` agent) | External cross-family adversarial pass, invoked by `/review` |
| Model policy | `MODEL-POLICY.md` | Role needs vs current bindings |
| Evals | `EVALS.md` | Per-role scenarios for comparing candidate models |

## Usage

```
/feature add Outlook Calendar integration

/bug Google OAuth succeeds but the session is missing after refresh

/review focus on security and regressions
```

- `/feature <description>` — explore → plan → bounded implementation → verify → independent review → fix real findings → summary.
- `/bug <symptom>` — reproduce → investigate (dual independent passes for hard bugs) → state root cause → smallest fix → verify → review.
- `/review [focus]` — review-only pass on the current diff; never edits. Runs the Claude reviewer and an adversarial Codex pass in parallel, cross-checks agreement, degrades gracefully to Claude-only if Codex is unavailable.

All three continue automatically through ordinary decisions and stop only for genuinely consequential product/architecture choices.

Agents are also usable individually outside the skills — e.g., delegate a search to `code-explorer` or ask `reviewer` for a one-off diff review via the Agent tool.

## Model upgrades

- **Workflows remain stable.** The skill bodies never name models.
- **Agent responsibilities remain stable.** Role definitions describe duties and rules, not model personalities.
- **Model bindings are replaceable.** They live only in `model:`/`effort:` frontmatter (see MODEL-POLICY.md for exact locations).
- **New models are evaluated by role.** Run the scenarios in `EVALS.md` on this codebase.
- **A binding changes only after the candidate performs better on the relevant role evals** — not because it topped a general leaderboard.

```
Upgrade models, not workflows.
Benchmark roles, not brands.
```

## External-model integration

The star topology makes non-Claude agents a drop-in: an external GPT/Codex agent can join as an independent reviewer or as an alternate implementation path, reporting to the same lead.

```
Lead
  ├── Claude-native agents
  └── external GPT/Codex reviewer
```

Prefer cross-family review when available — an independent model family has uncorrelated blind spots:

```
Claude implementation → GPT review
GPT implementation → Claude review
```

The lead remains responsible for deciding what to accept, from any family. Findings from an external reviewer get the same treatment as Claude reviewer findings: judged by the lead, real ones fixed, false positives dropped.

**Status: wired.** `/review` runs the Codex adversarial pass by default (via the `codex` plugin's `codex:codex-rescue` agent, Codex CLI installed on this machine), in parallel with the Claude reviewer and blind to its findings. Findings confirmed by both families are tagged `[both]` — highest confidence. If Codex is missing or fails, `/review` degrades to Claude-only and says so in the verdict. Codex as an alternate *implementation* path stays ad hoc (ask for it after `/feature`); no deeper integration until a workflow needs it.

## Design rules (why it looks like this)

- Explore first — never edit on a guess.
- Plan centrally — one owner of the decision.
- Edit narrowly — one implementer per code area, explicit boundary, never two agents on the same files concurrently.
- Verify mechanically — a dedicated agent runs the checks and reports facts.
- Review independently — a fresh, strong model with no loyalty to the plan.
- Scale depth with risk — small change, small verification; risky change, parallel review lenses.
- Read-only means read-only — enforced via `tools:` allowlists, not just prompts (explorer has no Bash at all; reviewer/test-runner have Bash for read/run but no Edit/Write).
