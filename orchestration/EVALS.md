# Role Evals

Lightweight manual evaluation suite for deciding whether a new model should take over a role. The question each eval answers:

> Is this new model better **for this specific role** on **this codebase**?

Not: "Is this model generally better?" — general benchmarks don't transfer reliably to a specific repo and a narrowly-scoped role.

The system is installed at user level and used across repos: run a role's eval **in the repo where that role does most of its work** (or in two contrasting repos if usage is spread). A candidate for the adversarial cross-family reviewer (see MODEL-POLICY.md) is scored with the **Reviewer eval** below, same criteria.

## How to run an eval

1. Pick the role and a candidate model.
2. Temporarily set the candidate in the role's `model:` frontmatter (see MODEL-POLICY.md for locations).
3. Run the role's scenarios below — same prompt for incumbent and candidate, ideally on the same git state.
4. Score each criterion 0–2 (see scoring). Sum per scenario, average across scenarios.
5. Candidate wins the role only if it beats the incumbent's total AND has no 0 on a critical criterion (marked ★).
6. Revert or keep the binding; record the outcome in MODEL-POLICY.md's binding history.

## Scoring

Per criterion:
- **0** — failed (missed it, wrong, or violated the role's rules)
- **1** — partial (found some, minor inaccuracies, noisy)
- **2** — solid (complete, accurate, concise)

Keep a small table per run:

```
Role: explorer   Candidate: <model>   Incumbent: <model>   Date: YYYY-MM-DD
Scenario | Criterion | Incumbent | Candidate
...
Total    |           |    X      |    Y
Decision: keep / switch — reason
```

---

## Explorer eval

Scenario (adapt the subject to a real area of this repo):

```
Find where authentication state is created, validated and persisted.
```

Run via: Agent tool, `subagent_type: code-explorer`.

Score:
- ★ found all important files
- found important symbols
- ★ no invented files or symbols
- correct data flow
- useful signal-to-noise ratio (report readable in one pass, no dumps)

## Implementer eval

Scenario:

```
Implement a small validation change without changing unrelated behavior.
```

(Pick a real, reversible change; run on a branch; discard after.)

Score:
- ★ correctness
- minimal diff
- ★ instruction adherence (stayed inside the stated boundary)
- tests added/updated for the behavior change
- no unrelated refactor

## Reviewer eval

Use a deliberately flawed patch: take a clean change and plant 1–2 real bugs (an off-by-one, a dropped error path, a missing null check) plus one removed test. Ask for a review of that diff.

Score:
- ★ finds the planted real bug(s)
- finds missing edge cases
- identifies the missing test
- ★ avoids false positives (no invented findings on the clean parts)

## Lead eval

Give the lead (via /feature on a scoped request) conflicting or incomplete explorer reports — e.g., hand-write two summaries where one names the wrong file or contradicts the other on data flow.

Score:
- synthesizes evidence correctly
- ★ notices the contradiction (verifies rather than picking one blindly)
- chooses appropriate architecture / boundary
- delegates appropriately (right agent, right scope, summarized context)
- ★ avoids premature implementation (no edits before plan)

## Bug-debugging eval

Reintroduce a previously-fixed real bug from git history (or plant one), then run `/bug <its symptom>`.

Score:
- reproduction quality (found or honestly reported as not reproducible)
- ★ root-cause accuracy
- smallest effective fix
- regression avoidance (nearby tests still pass; reviewer pass clean)

---

## Notes

- 2–3 scenarios per role beat 1; reuse the same scenarios across candidates so scores stay comparable.
- Cost/latency matter for explorer and test-runner: if scores tie, the cheaper/faster model wins those roles.
- Keep completed score tables below this line, newest first.
