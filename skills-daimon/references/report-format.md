# Markdown report format

Use this template. Keep it scannable, specific, honest. `analyze.py` builds the
skeleton; fill catalog-backed sections from live catalog results.

```
# Skills Daimon — last {window_days} days

📊 **Visual report:** <file:// URL printed by render_report.py> *(open in a browser)*

## 🏛  Verdict: <one named verdict from the ladder>

<One or two sentences explaining the strongest observed pattern, plain language.>

**Evidence:** <chip 1 · chip 2 · chip 3>  (2–4 compact counts)
**Next move:** *<exact phrase the user should say, no preamble>*

---

## ✨ Your archetype: <title>

> <one-line tagline grounded in the mix>

- **Why this title:** <work_recap.mix explanation>
- **Strength:** <one line>
- **Watch-out:** <one line>
- **Next ritual:** <one specific habit>

---

## 🎯 Primary next action

**<action title>**

Say:
> *"<exact phrase>"*

**Why:** <one or two sentences citing the hard count that triggered this>

**Source:** <`prompt-to-command` (sibling skill) | `learnings-keeper` (sibling skill) | catalog-backed skill name | Behavior recommendation · no install needed>

---

## 🧭 What you've been working on

<One-line read of `work_recap.mix`.>

| Project | Sessions | Tokens | Shipped | Focus |
|---|---|---|---|---|
| <basename> | <n> | <tokens> | <c/p> | <kind> |

(Top 3–5 from `work_recap.top_projects`.)

---

## ✨ Catalog-backed recommendations

Skills the user can install, drawn only from real catalog hits. **Catalog-verified, never generated.** **Aim for 3 (cap 5).** Always render full detail in markdown — TL;DR table AND numbered cards.

| Type | Name | Matches | Why |
|---|---|---|---|
| <type> | [<name>](<source_url>) | <job tag> | <≤10-word reason> |

(One row per rec, same order as cards below. Every row carries a real `source_url`. If a row lacks one, drop the link from **every** row. Never invent a URL.)

### 1. ●●● <job tag>
**Evidence:** <opening sentence = confidence reasoning; then hard counts>.
**Recommended <type>:** **<name>** — <one-line description from `get`>
**Install:**
- `<exact command 1>`
- `<exact command 2>`
**Provenance:** Catalog verified · not generated.

### 2. ... ### 3. ...

If no catalog-backed skill matched a job: *"No catalog-backed skill matched this pattern. No recommendation was generated."* Do not pad with weak matches.

---

## 🩺 Workflow signals

How you're working, scored only where there's a clear better way. Each row = 🟢 Good / 🟡 Watch / 🔴 Needs action / ⚪ No data.

| | Signal | Now | Verdict |
|---|---|---|---|
| 🟡 | File search: shell vs built-in tools | 17% via shell | Watch |

(One row per scorecard item; each carries `note` with denominator + `explain`.)

---

## ⚑ Coaching — small habits worth changing

### <card title>
**Evidence:** <hard count, with denominator if a rate>
**What we saw:** <one sentence>
**Why it matters:** <one sentence>
**Try this:** <one concrete behavior or command>
**Handoff:** <`prompt-to-command` / `learnings-keeper` / "No install needed.">

(Up to 3 cards. Cap at 3.)

---

## 🛠️ Worth building yourself

You do these a lot, but no existing skill matches.

- **<job tag>** — <one-line: evidence + why no skill matches>. Start one: `npx skills init <name>`

---

## 📈 Trends

(Render only when ≥3 distinct days in history. Else: *"Trends unlock after 3 distinct report days. Today's numeric snapshot has been saved."* Same-day reruns: *"Today's numeric snapshot was updated."*)

| Signal | Now | vs last run | Last 6 runs |
|---|---|---|---|
| Sessions finished | 80% | ↑ from 79 | (sparkline in HTML) |
```

## Links: table only

The user's terminal renders inline markdown links as `name (url)` — the URL shows.

- **Only the TL;DR table uses `[name](url)` links.**
- **Every row links — no lopsided tables.** Each rec carries a real `source_url`; if one genuinely has no URL, drop the link from **every** row. Never invent.
- **Body prose uses bold names only** — `**<name>**`, never a URL in detailed sections, "Worth building yourself", or install lines.
- **No links/references section at the end.**

## Confidence dots (numbered recommendations)

`●●●` high · `●●○` medium · `●○○` low. Dots go between the number and job tag:
`### 1. ●●● <job tag>`. **No separate "Confidence:" line** — the reasoning is the
opening sentence of the Evidence line. "Worth building yourself" uses a plain
heading (no dots).

The coaching section gets a hard visual break — full-width separator + top-level
`#` heading — so it reads as a distinct "now let's talk habits" part.

## Recommendation count / tone

- **Aim for 3 recommendations (min); cap at 5.** If only 1–2 strong jobs, widen the job search to next-best signals and re-query — every rec stays catalog-backed and count-grounded. If fewer than 3 real hits genuinely exist, say so plainly. Never pad, never invent.
- Lead with evidence. Mark confidence honestly — *"low"* is fine.
- Max 3 "worth building yourself" entries, max 3 coaching points.
- Evidence-bound habit calls are in scope (they cite a count). Count-free editorializing is out.
