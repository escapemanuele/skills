---
name: review
description: "[escapemanuele orchestration] Review-only workflow on the current diff — delegates to the independent reviewer agent plus a cross-family adversarial Codex pass, optionally with focused passes for correctness, security, and tests on large diffs. Never edits."
argument-hint: [optional focus, e.g. "focus on auth and security"]
disable-model-invocation: true
model: fable
effort: high
---

# /review — independent review of the current diff

Focus (optional): **$ARGUMENTS**

**Announce yourself first.** Before anything else, output exactly this line to the user:

> ⚙️ **escapemanuele orchestration** — `/review` (github.com/escapemanuele/skills)

This confirms the user's own orchestration skill is running — not Claude Code's built-in PR review, which shares the /review name. If you ever run a review and this line is missing, the built-in ran instead.

You are the lead. This workflow reviews; it never edits.

## Workflow

1. **Inspect the current diff** scope cheaply: `git status` and `git diff --stat` (plus `--staged` variants) to see size and touched areas. If the working tree is clean, diff against the default branch (`git diff main...HEAD`); if that's empty too, say there is nothing to review and stop.
2. **Do not edit anything.**
3. **Launch two independent review passes in parallel** (single message, two Agent calls, so neither sees the other's findings):
   - **Claude reviewer**: Agent tool, `subagent_type: reviewer`. Tell it exactly which diff to inspect (unstaged / staged / branch range) and pass along the user's focus if given.
   - **Adversarial cross-family pass (Codex)**: Agent tool, `subagent_type: "codex:codex-rescue"`. Ask it to adversarially review the exact same diff scope: hunt for correctness bugs, regressions, security/data-loss risks, missing tests, and broken edge cases; require `file:line` and a concrete failure scenario per finding, with a severity (Critical/High/Medium/Low); explicitly instruct it NOT to invent findings and to say plainly if it finds nothing significant.
   - If the Codex agent type is unavailable or fails (e.g., Codex CLI not installed on this machine), continue with the Claude pass alone and note the missing cross-check in the verdict. Never block the review on it.
4. **For large or high-risk diffs** (many files, security-sensitive areas, data migrations), optionally split the Claude side into separate parallel reviewer passes, each with one lens:
   - correctness
   - security
   - tests / regressions
   Small diffs get one Claude pass + one Codex pass — don't inflate the machinery.
5. **Synthesize.** Deduplicate findings across all passes. A finding reported independently by both model families is high-confidence — mark it. Where passes disagree (one flags, the other is silent, or they contradict), judge yourself by reading the code in question — you decide what is real, not either reviewer.
6. **Return findings ordered by severity** (Critical / High / Medium / Low).
7. **Include `file:line` references** wherever possible, each with a concrete failure scenario.
8. **If nothing significant was found, say so plainly.** "No significant issues found" is a complete, correct answer — do not pad it with manufactured nits.

## Final response format

```
## Findings
### Critical
- file:line — defect, failure scenario [both] / [claude] / [codex]
### High / Medium / Low
...
(omit empty severities; tag each finding with which reviewer(s) reported it — [both] = highest confidence)

## Verdict
"No significant issues found" or what must be fixed before merge.
One line on cross-model agreement (e.g., "Codex pass ran; N findings confirmed by both families" or "Codex unavailable — Claude-only review").
```

Findings are reported to the user; fixing them is a separate decision (the user can follow up with /feature, /bug, or a direct request).
