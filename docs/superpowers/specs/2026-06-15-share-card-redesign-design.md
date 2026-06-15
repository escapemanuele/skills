# Skills Daimon — Shareable Archetype Card Redesign

**Date:** 2026-06-15
**Status:** Design — awaiting review
**Scope:** `skills-daimon/bin/render_report.py` (share card SVG + share JS/CSS + simple-view share section), new tests.

## Problem

The current share card (`build_share_card_svg`, render_report.py:1185) is a
dashboard, not a story — the "Apple Replay failure mode." Users don't share it.
Concrete weaknesses, each tied to viral-card research (Spotify Wrapped, Strava,
Wordle, Duolingo, GitHub Skyline; Berger & Milkman virality; OG spec):

- **No hero focal point** — archetype title, tagline, work-mix bar, legend,
  grove, and level chip all compete; nothing stands out.
- **No identity framing** — reads as a label above stats, not "You're a …".
- **No status signal** — the strongest viral driver (a rare-feeling badge) absent.
- **No emotional hook** — flat grey palette, no surprising number, no wit.
- **Share = SVG download only** (`SHARE_JS`, render_report.py:996) — SVG won't
  paste into X / Instagram / LinkedIn; they reject or ignore it. No PNG, no
  one-tap share, no pre-filled caption.
- **One size only** (1080×1080) — missing 9:16 story and 1200×630 link sizes.

## Decisions (locked with user)

1. **Hero focal point:** big aggregate number — **session count over the
   window** ("475 / SESSIONS / in 28 days") — blown up as the single hero
   element, archetype as subtitle. User explicitly opted into exposing this
   aggregate total; it carries no project names/paths and stays non-identifying
   otherwise.
2. **Status pill:** **honest achievement tier** from real game state only —
   `Daimon Level N · K badges`, upgrading to `Balanced Grove · Level N` when all
   six tracks are ≥ Level 2. No fabricated percentile (charter: never invent).

## Design

### 1. Card layout — `build_share_card_svg`

Single-focal vertical hierarchy (1:1 / 9:16) and a horizontal variant (1200×630):

- Brand chip `🏛 SKILLS DAIMON` — small, top.
- **HERO** — session count in oversized gradient numerals + `SESSIONS` /
  `in {days} days` label. This is the one blown-up element.
- **Identity** — `You're a` + **{ARCHETYPE TITLE}** (bold) + italic tagline.
- **Dual-stat mix** — top two work-mix tracks as `46% dev · 42% writing`,
  de-emphasized relative to the hero. (Replaces the dense segmented bar +
  full legend.)
- **Achievement pill** — `✨ Daimon Level {level} · {badge_count} badges`, or
  `🌌 Balanced Grove · Level {level}` when `grove.balanced`.
- **Footer** — discreet `Generated locally from my own Claude Code usage ·
  skills-daimon` (no heavy watermark; watermarks suppress organic reach).

The Daimon Grove illustration is **dropped from the share card** to preserve a
single focal point (it remains in the full HTML report). This is the biggest
behavioral change and the main lever per the research.

### 2. Palette

Replace flat `#F8F7F3` background. Deep indigo→violet gradient
(`#4C1D95` → `#6D28D9` → `#7C3AED`) via an SVG `<linearGradient>`; hero numerals
in a bright contrasting gradient (e.g. `#F0ABFC` → `#FDE68A`); identity and pill
in high-contrast light type on the dark card. Keep the existing purple accent
lineage so it still reads as "Skills Daimon."

### 3. Sizes — one builder, three outputs

`build_share_card_svg(..., size="square"|"story"|"link")`:

| size   | dimensions  | layout                                            |
|--------|-------------|---------------------------------------------------|
| square | 1080×1080   | vertical hierarchy (default)                      |
| story  | 1080×1920   | same hierarchy, hero centered in safe zone        |
| link   | 1200×630    | horizontal: hero left, identity/mix/pill right    |

All critical content stays inside each platform's safe zone (story: central
~1080×1480). Shared sub-builders for hero / identity / mix / pill keep the three
layouts in sync.

### 4. PNG export — `SHARE_JS`

Rasterize the chosen SVG to PNG before download:
1. Serialize the SVG → `Blob` → object URL.
2. Draw onto a `<canvas>` sized to the SVG's intrinsic dimensions.
3. `canvas.toBlob(..., 'image/png')` → download `skills-daimon-archetype.png`.
Keep an SVG-download fallback if canvas/toBlob is unavailable.

### 5. One-tap share + caption

- Primary: `navigator.share({files:[pngFile], text, title})` from a real tap
  (HTTPS/secure-context only). Opens the native OS share sheet.
- Desktop fallback: X intent URL
  `https://twitter.com/intent/tweet?text=…&hashtags=SkillsDaimon` (text only;
  user attaches the downloaded PNG), plus the existing download.
- Caption (curiosity gap + identity): `"I'm a {archetype} this month — {N}
  sessions in. What's your Claude Code archetype?"` Hashtag `#SkillsDaimon`.

### 6. Out of scope

- **og:image / link unfurls** — the report is a local `file://` page; there is
  no URL for a platform to unfurl. Revisit only if reports are ever hosted.
- No change to what data is computed; this is presentation + export only.
- Privacy contract otherwise unchanged: no project names/paths, no per-project
  counts, no recommendations/coaching on the card.

## Testing

- Unit (Python): `build_share_card_svg` for each `size` returns well-formed SVG
  with the correct `viewBox`/dimensions; hero shows the session count; pill
  shows Balanced Grove iff `grove.balanced`; archetype title/tagline escaped;
  no identifying fields (assert none of project paths/recs strings leak in).
- Unit: pill text logic (level+badges vs Balanced Grove) as a pure helper.
- Manual: render a report, open HTML, confirm PNG downloads and the share
  button opens the native sheet (or X intent on desktop).

## Files

- `skills-daimon/bin/render_report.py` — `build_share_card_svg`, `SHARE_CSS`,
  `SHARE_JS`, simple-view share section.
- `skills-daimon/tests/test_share_card.py` — new.
- Mirror to installed copy `~/.agents/skills/skills-daimon/` after.
