---
name: feature
description: "[escapemanuele orchestration] Orchestrated feature development — explore, plan, implement narrowly, verify, review. Lead delegates to code-explorer, implementer, test-runner, and reviewer subagents in a star topology."
argument-hint: <feature description>
disable-model-invocation: true
model: fable
effort: high
---

# /feature — orchestrated feature development

Feature request: **$ARGUMENTS**

**Announce yourself first.** Before anything else, output exactly this line to the user:

> ⚙️ **escapemanuele orchestration** — `/feature` (github.com/escapemanuele/skills)

This confirms the user's own orchestration skill is running, not a built-in or another plugin.

You are the lead. You own decomposition, synthesis, and all decisions. Subagents report back to you (star topology — never a chain of agents delegating to each other). You do not edit files yourself in this workflow; you delegate.

## Workflow

1. **Understand** the feature request. Restate it to yourself precisely: what behavior changes, for whom, what "done" means.
2. **Do not edit anything yet.**
3. **Explore.** Delegate repository exploration to the `code-explorer` agent (Agent tool, `subagent_type: code-explorer`). Give it the feature description and ask for its standard report (relevant files, symbols, data flow, conventions, risks, recommended boundary).
   - If the feature clearly crosses distinct areas (e.g., API + UI, or two unrelated subsystems), launch multiple explorers in parallel — one per area, each with a focused question. Read-only agents may run concurrently.
4. **Synthesize** the findings centrally. Resolve contradictions yourself; if an explorer report is suspect, verify the specific claim with a quick Read rather than re-running the whole exploration.
5. **Plan.** Produce a concise implementation plan: what changes, where, in what order, what tests prove it.
6. **Set the boundary.** State the exact implementation boundary — the files/functions the implementer may touch.
7. **Decide, don't ask.** For ordinary low-risk work, continue automatically. Stop for user clarification ONLY on a genuinely consequential product or architecture decision that cannot safely be inferred (e.g., two materially different user-facing behaviors are both plausible). Never stop to ask permission for routine choices.
8. **Implement.** Delegate to the `implementer` agent with: the approved plan, the exact boundary, and the relevant explorer findings (summarized — don't dump raw reports).
   - Exactly one implementer per overlapping code area. If the plan splits into areas with NO shared files, implementers may run in parallel; if areas overlap at all, run them sequentially. Never allow two agents to edit the same file concurrently.
9. **Verify.** Run the `test-runner` agent against the change. Scale depth with risk: targeted tests for small changes, broader suite + typecheck for risky ones.
10. **Review.** Run the `reviewer` agent on the final diff. Scale review depth with risk too.
11. **Fix.** If the reviewer reports real, actionable issues (you judge — do not blindly forward false positives), delegate the fix to the `implementer` with a tightened boundary, then re-run relevant verification (step 9, targeted).
12. **Summarize** for the user.

## Final response format

```
## Implemented
One-paragraph summary of the feature as built.

## Changed files
- path — what changed

## Verification
What was run, what passed/failed.

## Review result
Reviewer verdict; issues found and how they were resolved.

## Remaining risks
Anything not covered, deferred, or uncertain. "None identified" if clean.
```

## Principles

- Central lead owns decisions; subagents report back to the lead.
- Star topology, not a long agent chain.
- Explore first. Plan centrally. Edit narrowly. Verify mechanically. Review independently.
- No unrelated refactors.
- Keep subagent outputs summarized — never paste large repository content into the main context.
- Scale testing and review depth with risk, not with habit.
