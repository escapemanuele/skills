# Model Policy

Roles are stable; model bindings are replaceable. Each role below states what it *needs* (permanent) and which model currently *holds* it (temporary). When a better model appears, change the binding — never the workflow or the role definition.

Bindings live in exactly three kinds of places:
- `~/.claude/agents/*.md` — `model:` and `effort:` frontmatter (explorer, implementer, reviewer, test-runner)
- `~/.claude/skills/{feature,bug,review}/SKILL.md` — `model:` and `effort:` frontmatter (lead)
- `~/.claude/skills/review/SKILL.md` workflow step 3 — the adversarial cross-family reviewer's agent reference (currently `codex:codex-rescue`); an external CLI can't be bound via frontmatter, so this is the one sanctioned exception to "no model names in workflow bodies"

Change a binding by editing those places only. Otherwise, workflow bodies and agent responsibilities must not mention model names.

---

## Lead

Needs:
- architectural reasoning
- decomposition
- synthesis of conflicting evidence
- delegation
- resistance to premature implementation

Current model:
- `fable`
- high reasoning/effort

Bound in: `~/.claude/skills/feature/SKILL.md`, `.claude/skills/bug/SKILL.md`, `.claude/skills/review/SKILL.md`

## Explorer

Needs:
- strong repository navigation
- high recall
- accurate summaries
- speed and reasonable cost

Current model:
- `sonnet`
- medium reasoning/effort

Bound in: `~/.claude/agents/code-explorer.md`

## Implementer

Needs:
- reliable code editing
- instruction adherence
- minimal diffs
- test awareness

Current model:
- `sonnet`
- high reasoning/effort

Bound in: `~/.claude/agents/implementer.md`

## Reviewer

Needs:
- independence
- adversarial reasoning
- bug detection
- low false-positive rate

Current model:
- `fable`
- high reasoning/effort

Bound in: `~/.claude/agents/reviewer.md`

## Test Runner

Needs:
- efficient command execution
- accurate failure interpretation

Current model:
- `sonnet`
- medium reasoning/effort

Bound in: `~/.claude/agents/test-runner.md`

## Adversarial Reviewer (cross-family)

Needs:
- a different model family from the implementer and primary reviewer (uncorrelated blind spots)
- adversarial reasoning
- bug detection with `file:line` precision
- low false-positive rate

Current binding:
- OpenAI Codex (Codex CLI via `codex` plugin, `codex:codex-rescue` agent)

Bound in: `~/.claude/skills/review/SKILL.md` (workflow step 3). Optional by design — `/review` degrades to Claude-only when unavailable.

---

## Binding rules

1. **Model bindings change independently from workflows.** A model upgrade is an edit to `model:`/`effort:` frontmatter, nothing else.
2. **Use model-family aliases** (`sonnet`, `opus`, `haiku`, `fable`), not dated model IDs. Aliases track the current best model in the family automatically. Pin an exact dated ID only if a specific regression forces it, and record why here.
3. **One role, one binding.** Don't give the same role different models in different skills without a reason recorded here.
4. **Upgrade only on evidence.** A new model takes over a role only after beating the incumbent on that role's evals (see `EVALS.md`) on this codebase. "Newer" or "bigger" is not evidence.
5. **Record changes.** When a binding changes, append a dated line below.

## Binding history

- 2026-07-14 — System moved from Vault project level to user level (`~/.claude/`); available in all repos. Adversarial cross-family reviewer added to /review: OpenAI Codex via `codex:codex-rescue`.
- 2026-07-14 — Initial policy: lead=fable/high, explorer=sonnet/medium, implementer=sonnet/high, reviewer=fable/high, test-runner=sonnet/medium.
