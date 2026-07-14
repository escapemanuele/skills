---
name: bug
description: Orchestrated debugging — reproduce, investigate read-only, state root cause, apply smallest fix, verify, review. Lead delegates to code-explorer, implementer, test-runner, and reviewer subagents.
argument-hint: <bug description>
disable-model-invocation: true
model: fable
effort: high
---

# /bug — orchestrated debugging

Bug report: **$ARGUMENTS**

You are the lead. You own the diagnosis and all decisions. Subagents report back to you (star topology). No speculative rewrites — this workflow ends in the smallest fix that addresses the root cause, or an honest report that the root cause is not yet established.

## Workflow

1. **Understand** the reported behavior: what happens, what was expected, when it started if known.
2. **Do not edit anything yet.**
3. **Reproduce.** Try to identify how the bug can be reproduced — an existing test, a command, a minimal input. A reliable reproduction is the strongest evidence you can get; note it explicitly if you find one, and note if you can't.
4. **Investigate.** Delegate read-only investigation to the `code-explorer` agent (Agent tool, `subagent_type: code-explorer`) with a focused question about the failing behavior.
5. **For difficult bugs**, run two independent exploration passes with different investigative goals, in parallel, e.g.:
   - one explorer traces the runtime/data flow of the failing path (where the value is created, mutated, persisted, lost);
   - one explorer inspects tests, recent changes, assumptions, state transitions, and likely edge cases around the area.
   Independent passes exist to give you uncorrelated evidence — don't share one's hypothesis with the other.
6. **Synthesize** the evidence centrally. Weigh contradictions; verify load-bearing claims yourself with a quick Read if needed.
7. **State the likely root cause before any implementation.** If the evidence doesn't support a root cause yet, iterate on investigation — do not fix symptoms.
8. **Choose the smallest fix** that addresses the root cause (not the symptom, not a rewrite).
9. **Fix.** Delegate the bounded fix to the `implementer` agent: root cause statement, exact fix, exact boundary. Ask it to add/adjust a test that fails without the fix when feasible.
10. **Verify.** Run the `test-runner` agent: confirm the bug is fixed (the reproduction from step 3 now passes) and nearby tests still pass.
11. **Review.** Run the `reviewer` agent on the diff, focused on regressions and edge cases around the fix.
12. **Fix real review findings** (you judge which are real) via the `implementer`, then re-run relevant verification.

## Final response format

```
## Root cause
What was actually wrong, with file:line.

## Fix
What the fix does and why it addresses the root cause.

## Changed files
- path — what changed

## Verification
Reproduction result before/after where available; tests run and outcomes.

## Review result
Reviewer verdict; issues found and how resolved.

## Remaining uncertainty
What is still unknown or unverified. "None" if genuinely none.
```

## Principles

- Evidence before diagnosis; diagnosis before fix.
- Smallest fix that addresses the root cause. No speculative rewrites.
- One implementer per code area; never concurrent edits to the same files.
- Keep subagent outputs summarized; don't flood the main context.
