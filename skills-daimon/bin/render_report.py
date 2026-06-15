#!/usr/bin/env python3
"""
skills-daimon HTML report renderer.

Takes a JSON payload (the analysis Claude produced + the deterministic counts
from scan.py) and writes a single self-contained HTML file: inline CSS, inline
SVG charts, zero network calls. Opens offline; nothing leaves the machine.

Usage:
    python3 render_report.py payload.json           # read from file
    python3 render_report.py < payload.json          # read from stdin

Payload schema (all keys optional except meta):
{
  "meta": {"days": 14, "sessions": 138, "projects": 11,
            "date": "2026-05-21", "catalogs": ["skills.sh", ...]},
  "recommendations": [
    {"rank": 1, "confidence": "high|med|low", "type": "skill|plugin|agent",
     "name": "...", "job": "...", "evidence": "...", "description": "...",
     "install": ["cmd1", "cmd2"], "source_url": "..."}
  ],
  "gaps": [{"tag": "...", "note": "...", "init": "npx skills init ..."}],
  "coaching": [{"title": "...", "evidence": "<hard count>", "saw": "...", "costs": "...", "better": "..."}],
  "charts": {
     "bash_verbs_top": {"ls": 212, ...},
     "tool_use_top": {"Bash": 1112, ...},
     "native_bypass": {"bypass_total": 214, "native_total": 864,
                        "bypass_calls": {"grep": 109, ...}}
  }
}

Writes to ~/.claude/skills/skills-daimon/reports/skills-daimon-<date>.html and prints
the path plus a clickable file:// URL.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import re
import sys
from pathlib import Path

# Shared secret redactor (applied at the write boundary).
sys.path.insert(0, str(Path(__file__).parent))
from redact import redact_in  # noqa: E402


# --- palette (charter v2) ---------------------------------------------------
ACCENT = "#6D28D9"        # primary
ACCENT_SOFT = "#EDE9FE"
INK = "#171717"
MUTED = "#6B7280"
BG = "#F8F7F3"
GAP = "#E5E7EB"
GOOD = "#0F766E"
WATCH = "#B45309"
BAD = "#B42318"           # "needs_action"
INFO = "#2563EB"
BAR = "#6D28D9"
BAR_WARN = WATCH
CONF = {"high": GOOD, "med": WATCH, "low": MUTED}
CONF_DOTS = {"high": "●●●", "med": "●●○", "low": "●○○"}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _int(v) -> int:
    """Tolerant int coercion — never let a stray non-numeric value crash a render."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# Control chars XML 1.0 forbids even when escaped; they make an SVG unopenable.
_XML_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _svg_text(s) -> str:
    """Escape text for inline SVG and strip XML-illegal control chars so the
    downloadable poster always parses as well-formed XML."""
    return _XML_BAD_CHARS.sub("", esc(s))


def safe_metric_text(s) -> str:
    text = str(s if s is not None else "")
    if "0% of 0" in text or "0 of 0 sessions" in text:
        return "Not enough session data to evaluate this area."
    return text


def esc_metric(s) -> str:
    return esc(safe_metric_text(s))


# --------------------------------------------------------------------------
# Anthropic /insights cross-reference
# --------------------------------------------------------------------------
def _extract_insights_sections(html_path: Path) -> dict | None:
    """Lift the 'New Ways to Use Claude Code' and 'On the Horizon' sections
    from a locally cached `/insights` report. Returns `{patterns, horizon}`
    as raw HTML snippets, or None if the file is missing / unreadable / the
    Anthropic layout has changed enough that the anchors aren't found.

    Strips inline `on*` handlers and `<button>` elements so the embedded
    snippet doesn't pull in /insights' JS (we ship none).
    """
    try:
        raw = html_path.read_text(encoding="utf-8")
    except Exception:
        return None
    out = {}
    for anchor, key in [("section-patterns", "patterns"),
                        ("section-horizon",  "horizon")]:
        start = re.search(rf'<h2 id="{anchor}">.*?</h2>', raw, re.DOTALL)
        if not start:
            continue
        tail = raw[start.end():]
        nxt = re.search(r'<h2\b|<div class="fun-ending"\b', tail)
        end_pos = start.end() + (nxt.start() if nxt else len(tail))
        snippet = raw[start.start():end_pos]
        snippet = re.sub(r'<button\b[^>]*>.*?</button>', '', snippet, flags=re.DOTALL)
        snippet = re.sub(r'\son\w+="[^"]*"', '', snippet)
        out[key] = snippet.strip()
    return out if out else None


def bar_chart(data: dict, title: str, top: int = 10, color: str = BAR) -> str:
    """Horizontal bar chart as inline SVG from a {label: count} dict."""
    items = sorted(data.items(), key=lambda kv: kv[1], reverse=True)[:top]
    if not items:
        return ""
    maxv = max(v for _, v in items) or 1
    row_h, gap, label_w, bar_w = 26, 8, 130, 360
    width = label_w + bar_w + 60
    height = len(items) * (row_h + gap) + 10
    rows = []
    for i, (label, v) in enumerate(items):
        y = i * (row_h + gap) + 5
        w = max(2, int(bar_w * v / maxv))
        rows.append(
            f'<text x="{label_w - 8}" y="{y + row_h * 0.68}" '
            f'text-anchor="end" class="bl">{esc(label)}</text>'
            f'<rect x="{label_w}" y="{y}" width="{w}" height="{row_h}" '
            f'rx="4" fill="{color}"/>'
            f'<text x="{label_w + w + 6}" y="{y + row_h * 0.68}" '
            f'class="bv">{v}</text>'
        )
    return (
        f'<div class="chart"><h3>{esc(title)}</h3>'
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'preserveAspectRatio="xMinYMin meet" role="img">{"".join(rows)}</svg></div>'
    )


# Charter labels: Good / Watch / Needs action / No data.
# Legacy keys (`warn`, `bad`) alias to `watch` / `needs_action` so existing
# payloads still render correctly.
VERDICT_LABEL = {
    "good": "✓ Good",
    "watch": "↑ Watch",
    "warn": "↑ Watch",
    "needs_action": "↑ Needs action",
    "bad": "↑ Needs action",
    "no_data": "— No data",
}


def _verdict_class(v: str) -> str:
    """Map a payload verdict to the CSS class root."""
    if v in ("watch", "warn"):
        return "watch"
    if v in ("needs_action", "bad"):
        return "bad"
    if v == "no_data":
        return "nodata"
    return v or "watch"


def _fmt_num(v):
    """Compact number rendering for sparkline tooltips and deltas."""
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".") or "0"
    return str(v)


# Default direction-of-goodness per known history_key. Higher value = better,
# or lower value = better. Used to color deltas / sparklines correctly.
HIGHER_BETTER_KEYS = {"outcome_finished_pct", "memory_rate_pct"}
LOWER_BETTER_KEYS = {
    "risky_git_count", "search_shell_pct", "bash_error_pct",
    "claudemd_missing", "unsaved_prompts",
}


def _direction_for(item: dict) -> str | None:
    """Return 'higher_better' / 'lower_better' / None for a scorecard item."""
    d = (item.get("direction") or "").strip().lower()
    if d in ("higher_better", "lower_better"):
        return d
    key = item.get("history_key")
    if key in HIGHER_BETTER_KEYS:
        return "higher_better"
    if key in LOWER_BETTER_KEYS:
        return "lower_better"
    return None


def _trend_class(current, prior, direction: str | None) -> str:
    """'good' if movement is in the better direction, 'bad' otherwise, '' if neutral."""
    if direction is None or current is None or prior is None:
        return ""
    try:
        if current == prior:
            return ""
        better = (current > prior) if direction == "higher_better" else (current < prior)
    except TypeError:
        return ""
    return "good" if better else "bad"


def sparkline(values, w=72, h=18, trend_class: str = "") -> str:
    """Inline-SVG sparkline. Requires ≥3 distinct points or returns empty.

    `trend_class` ('good'/'bad'/'') styles the stroke via CSS so the line color
    reflects whether the metric is heading the right way.
    """
    if not values or len(values) < 3:
        return ""
    lo, hi = min(values), max(values)
    rng = max(1, hi - lo)
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = i * (w - 2) / (n - 1) + 1
        y = h - 2 - (v - lo) * (h - 4) / rng
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    cls = f"sline {trend_class}".strip()
    return (
        f'<svg class="{cls}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img"><polyline fill="none" stroke="currentColor" '
        f'stroke-width="1.5" points="{poly}"/></svg>'
    )


def _delta_html(values: list, current, direction: str | None = None) -> str:
    """\"↑ from 10\" delta. Color reflects whether the movement is in the
    metric's better direction (good/bad), not just the arrow direction."""
    if not values or current is None:
        return ""
    prior = None
    for v in reversed(values[:-1]):  # exclude current run if present
        if v != current and v is not None:
            prior = v
            break
    if prior is None or prior == current:
        return ""
    try:
        arrow = "↑" if current > prior else "↓"
    except TypeError:
        return ""
    cls = _trend_class(current, prior, direction)
    if not cls:
        # Fall back to direction-only color (legacy behavior) when caller
        # has not declared a direction.
        cls = "up" if current > prior else "down"
    return f' <span class="delta {cls}">{arrow} from {_fmt_num(prior)}</span>'


def scorecard_strip(items: list, history_by_key: dict | None = None) -> str:
    """Health-check rows. Each item: {label, value, verdict, note, explain,
    history_key?, current_number?}. Sparkline + delta render when the row
    carries a `history_key` and ≥3 prior days exist for that key.
    """
    if not items:
        return ""
    history_by_key = history_by_key or {}
    rows = []
    for it in items:
        v = it.get("verdict", "warn")
        vlabel = VERDICT_LABEL.get(v, "")
        note = esc_metric(it.get("note", ""))
        explain = esc_metric(it.get("explain", ""))
        body = note
        if explain:
            body += f'<div class="scexplain">{explain}</div>'
        # Trend bits (only when history has the key)
        key = it.get("history_key")
        series = history_by_key.get(key) if key else None
        cur = it.get("current_number")
        direction = _direction_for(it)
        # Compute the "is this trending the right way?" class once; reuse it
        # for both the sparkline stroke and the delta chip.
        trend_cls = ""
        if series and cur is not None:
            prior = next((v for v in reversed(series[:-1]) if v != cur and v is not None), None)
            trend_cls = _trend_class(cur, prior, direction)
        spark = sparkline(series, trend_class=trend_cls) if series else ""
        delta = _delta_html(series, cur, direction) if series and cur is not None else ""
        rows.append(
            '<details class="scrow">'
            '<summary>'
            '<span class="scchev">▸</span>'
            f'<div class="scmain"><div class="sclabel">{esc_metric(it.get("label", ""))}</div></div>'
            f'<div class="scval">{esc_metric(it.get("value", ""))}{delta}</div>'
            f'<div class="scspark">{spark}</div>'
            f'<span class="verdict {_verdict_class(v)}">{vlabel}</span>'
            '</summary>'
            f'<div class="scbody">{body}</div>'
            '</details>'
        )
    return '<div class="card scorecard">' + "".join(rows) + "</div>"


def rec_card(r: dict) -> str:
    conf = r.get("confidence", "med")
    dots = CONF_DOTS.get(conf, CONF_DOTS["med"])
    color = CONF.get(conf, CONF["med"])
    name = esc(r.get("name", "?"))
    url = r.get("source_url")
    name_html = f'<a href="{esc(url)}">{name}</a>' if url else name
    install = "".join(
        f'<code>{esc(c)}</code>' for c in (r.get("install") or [])
    )
    return (
        '<div class="card rec">'
        f'<div class="rhead"><span class="dots" style="color:{color}">{dots}</span>'
        f'<span class="rtype">{esc(r.get("type", "skill"))}</span>'
        f'<span class="rname">{name_html}</span>'
        f'<span class="rjob">{esc(r.get("job", ""))}</span></div>'
        f'<p class="ev"><b>Evidence.</b> {esc(r.get("evidence", ""))}</p>'
        + (
            '<details class="rec-desc">'
            '<summary>What it does</summary>'
            f'<p class="desc">{esc(r.get("description", ""))}</p>'
            '</details>' if r.get("description") else ""
        )
        + f'<div class="install">{install}</div>'
        '</div>'
    )


def coach_card(c: dict) -> str:
    handoff = (c.get("handoff") or "").strip()
    handoff_html = (
        f'<p><span class="tag hand-t">Handoff</span> {esc(handoff)}</p>'
        if handoff else ""
    )
    return (
        '<div class="card coach">'
        f'<h4>{esc(c.get("title", ""))}</h4>'
        f'<p><span class="tag ev-t">Evidence</span> {esc(c.get("evidence", ""))}</p>'
        f'<p><span class="tag saw-t">What we saw</span> {esc(c.get("saw", ""))}</p>'
        f'<p><span class="tag cost-t">Why it matters</span> {esc(c.get("costs", ""))}</p>'
        f'<p><span class="tag best-t">Try this</span> {esc(c.get("better", ""))}</p>'
        f'{handoff_html}'
        '</div>'
    )


def fmt_tokens(n: int) -> str:
    """Human-readable token count: 12_400_000 -> '12.4M'."""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


KIND_ICON = {"dev": "⚙", "writing": "✍", "data": "▦", "ops": "◷", "other": "•"}


def recap_strip(w: dict) -> str:
    """Top projects (kind-tagged) + a work-mix bar."""
    if not w:
        return ""
    import os
    rows = []
    any_shipped = any(
        (p.get("commits") or 0) or (p.get("pushes") or 0)
        for p in (w.get("top_projects") or [])
    )
    for p in (w.get("top_projects") or [])[:5]:
        path = p.get("path", "")
        name = os.path.basename(path.rstrip("/")) or path
        kind = p.get("kind", "other")
        icon = KIND_ICON.get(kind, "•")
        commits = int(p.get("commits") or 0)
        pushes = int(p.get("pushes") or 0)
        shipped_cell = (
            f'<td class="num" title="commits / pushes (from session-meta)">'
            f'{commits}c / {pushes}p</td>'
        ) if any_shipped else ""
        rows.append(
            f'<tr><td>{icon} {esc(name)}</td><td class="num">{esc(p.get("sessions", 0))}</td>'
            f'<td class="num">{esc(fmt_tokens(p.get("tokens", 0)))}</td>'
            f'{shipped_cell}'
            f'<td><span class="kind k-{esc(kind)}">{esc(kind)}</span></td></tr>'
        )
    mix = w.get("mix") or {}
    mix_bar = "".join(
        f'<span class="seg k-{esc(k)}" style="width:{v}%" title="{esc(k)} {v}%"></span>'
        for k, v in mix.items() if v
    )
    mix_legend = " · ".join(f'{esc(k)} {v}%' for k, v in mix.items() if v)
    shipped_th = '<th class="num">Shipped</th>' if any_shipped else ""
    return (
        '<div class="card recap">'
        f'<div class="mixbar">{mix_bar}</div><p class="cap">{esc(mix_legend)}</p>'
        '<table class="rt"><thead><tr><th>Project</th><th class="num">Sessions</th>'
        f'<th class="num">Tokens</th>{shipped_th}<th>Focus</th></tr></thead><tbody>'
        f'{"".join(rows)}</tbody></table>'
        '</div>'
    )


def gap_item(g: dict) -> str:
    init = g.get("init")
    init_html = f' <code>{esc(init)}</code>' if init else ""
    return (
        f'<li><b>{esc(g.get("tag", ""))}</b> — {esc(g.get("note", ""))}{init_html}</li>'
    )


def hero_card(verdict: dict, archetype: dict, meta: dict,
              level_chip_inner: str = "") -> str:
    """Combined hero: archetype + verdict + Daimon Level + evidence chips +
    expandable archetype details. Replaces the previous separate hero+archetype
    cards so the top of the report is one block instead of two."""
    verdict = verdict or {}
    archetype = archetype or {}
    if not verdict.get("name") and not archetype.get("title"):
        return ""

    days = esc(meta.get("days", "14"))
    date = esc(meta.get("date", ""))
    chips = "".join(
        f'<span class="chip">{esc(c)}</span>' for c in (verdict.get("evidence_chips") or [])
    )
    # Archetype identity (no separate card)
    arch_title = esc(archetype.get("title", ""))
    arch_tag = esc(archetype.get("tagline", ""))
    # Verdict name + summary
    v_name = esc(verdict.get("name", ""))
    v_sum = esc(verdict.get("summary", ""))

    # Expandable details: why/strength/watch-out/next ritual — collapsed by
    # default so the top stays calm.
    detail_rows = []
    for label, key in (("Why this title", "why"), ("Strength", "strength"),
                       ("Watch-out", "watch_out"), ("Next ritual", "next_ritual")):
        val = (archetype.get(key) or "").strip()
        if val:
            detail_rows.append(
                f'<dt class="arch-dt">{label}</dt><dd class="arch-dd">{esc(val)}</dd>'
            )
    details_html = (
        '<details class="hero-details">'
        '<summary>About this archetype</summary>'
        f'<dl class="arch-dl">{"".join(detail_rows)}</dl>'
        '</details>'
    ) if detail_rows else ""

    # Daimon Level chip: accept either inner HTML (legacy) or a full
    # <div class="daimon-chip">…</div> block (current renderer).
    chip_html = ""
    if level_chip_inner:
        chip_html = (level_chip_inner
                     if 'class="daimon-chip"' in level_chip_inner
                     else f'<div class="daimon-chip" title="Daimon Level">{level_chip_inner}</div>')

    return (
        '<section class="hero-card">'
        f'<div class="hv-sub">Skills Daimon · Last {days} days · generated {date}</div>'
        f'{chip_html}'
        f'<h1 class="hero-arch-title">{arch_title}</h1>'
        f'<p class="hero-arch-tag">{arch_tag}</p>'
        '<div class="hero-verdict-row">'
        f'<span class="hero-verdict-label">Verdict</span>'
        f'<span class="hero-verdict-name">{v_name}</span>'
        '</div>'
        f'<p class="hero-summary">{v_sum}</p>'
        f'<div class="hv-chips">{chips}</div>'
        f'{details_html}'
        '</section>'
    )


# Back-compat shims — kept so any external caller still works.
def hero_verdict_card(verdict: dict, meta: dict) -> str:
    return hero_card(verdict, {}, meta, "")


def primary_action_card(a: dict) -> str:
    """The single most-important card above the fold."""
    if not a or not a.get("title"):
        return ""
    phrase = (a.get("phrase") or "").strip()
    why = esc(a.get("why", ""))
    source = esc(a.get("source", ""))
    phrase_html = f'<code class="pa-phrase">{esc(phrase)}</code>' if phrase else ""
    return (
        '<section class="primary-action card">'
        '<div class="pa-tag">Primary next action</div>'
        f'<h2 class="pa-title">{esc(a.get("title"))}</h2>'
        f'{phrase_html}'
        f'<p class="pa-why"><b>Why.</b> {why}</p>'
        f'<p class="pa-source"><b>Source.</b> {source}</p>'
        '</section>'
    )


_DEFAULT_TRUST = [
    "Local only — no network calls during scan, render, history, or redaction.",
    "Redaction applied at every write boundary (Authorization/Bearer, sk-…, gh tokens, AWS keys, basic-auth URLs, password=/token=, long hex).",
    "History stores numbers only — no commands, paths, prompts, or session IDs.",
    "Recommendations are catalog-backed; coaching points cite hard counts.",
    "Stuck-loop entries on disk: hash + 3-word summary only.",
    "Coverage shown on every rate (X of N labeled); low data → No data, not weak inference.",
    "No invented skills. If no catalog match, the report says so.",
]


def trust_ledger_card(items: list | None) -> str:
    bullets = items if items else _DEFAULT_TRUST
    rows = "".join(f"<li>{esc(b)}</li>" for b in bullets)
    return (
        '<section class="trust card">'
        '<h2 class="trust-title">🔒 Trust ledger</h2>'
        f'<ul class="trust-list">{rows}</ul>'
        '</section>'
    )


def archetype_card(a: dict) -> str:
    """Five-part archetype card per charter."""
    if not a or not a.get("title"):
        return ""
    parts = []
    parts.append(f'<div class="archlabel">Your archetype</div>')
    parts.append(f'<div class="archtitle">{esc(a.get("title"))}</div>')
    if a.get("tagline"):
        parts.append(f'<div class="archtag">{esc(a.get("tagline"))}</div>')
    rows = []
    for label, key in (("Why this title", "why"), ("Strength", "strength"),
                       ("Watch-out", "watch_out"), ("Next ritual", "next_ritual")):
        v = (a.get(key) or "").strip()
        if v:
            rows.append(
                f'<dt class="arch-dt">{label}</dt><dd class="arch-dd">{esc(v)}</dd>'
            )
    if rows:
        parts.append(f'<dl class="arch-dl">{"".join(rows)}</dl>')
    return '<section class="archetype-card card">' + "".join(parts) + '</section>'


CSS = """
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:%(INK)s;-webkit-font-smoothing:antialiased;background:%(BG)s}
.wrap{max-width:920px;margin:0 auto;padding:32px 24px 64px}
/* sidebar nav — fixed left, icon strip; labels slide in on hover */
.sidebar{position:fixed;top:24px;left:14px;display:flex;flex-direction:column;gap:6px;
  padding:10px 8px;background:rgba(255,253,248,0.92);
  border:1px solid #e7d6b6;border-radius:14px;
  box-shadow:0 2px 10px rgba(124,74,40,0.08);
  z-index:50;backdrop-filter:saturate(140%%) blur(6px)}
.snav{display:flex;align-items:center;gap:10px;
  text-decoration:none;color:%(INK)s;
  padding:7px 10px;border-radius:10px;
  font-size:13px;font-weight:600;
  transition:background .12s,transform .12s}
.snav:hover{background:#fff4dc;transform:translateX(2px)}
.snav.active{background:#fff4dc;color:#7C4A28;font-weight:700}
.snav-ext{margin-top:6px;border-top:1px solid #ead6b5;padding-top:11px}
.snav-out{font-size:11px;color:%(MUTED)s;margin-left:2px}
.snav.active .snav-lbl{max-width:160px;opacity:1}
.snav.active::before{content:"";position:absolute;left:-8px;top:8px;bottom:8px;
  width:3px;border-radius:2px;background:#7C4A28}
.snav{position:relative}
.snav-ico{font-size:16px;line-height:1;width:18px;text-align:center}
.snav-lbl{white-space:nowrap;max-width:160px;opacity:1}
section{scroll-margin-top:24px}
@media(max-width:1100px){
  .sidebar{position:static;flex-direction:row;flex-wrap:wrap;justify-content:center;
    margin:18px auto 0;max-width:920px;width:fit-content}
  .snav-lbl{max-width:160px;opacity:1}
}
header.hero{background:linear-gradient(135deg,%(ACCENT)s,#7c3aed);color:#fff;
  border-radius:18px;padding:28px 30px;margin-bottom:28px}
header.hero h1{margin:0 0 4px;font-size:26px;letter-spacing:-.3px}
header.hero .sub{opacity:.9;font-size:14px}
.stats{display:flex;gap:28px;margin-top:18px;flex-wrap:wrap}
.stats .s b{font-size:24px;display:block;line-height:1}
.stats .s span{font-size:12px;opacity:.85;text-transform:uppercase;letter-spacing:.5px}
.srclabel{margin-top:18px;font-size:12px;font-weight:600;text-transform:uppercase;
  letter-spacing:.5px;opacity:.95}
.srclabel span{font-weight:400;text-transform:none;letter-spacing:0;opacity:.8}
.badges{margin-top:8px;display:flex;gap:8px;flex-wrap:wrap}
.badge{background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.25);
  padding:3px 10px;border-radius:999px;font-size:12px}
.archetype{display:flex;align-items:center;gap:18px;margin-top:20px;
  background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);
  border-radius:14px;padding:16px 18px}
.archlabel{font-size:11px;text-transform:uppercase;letter-spacing:1px;opacity:.85}
.archtitle{font-size:22px;font-weight:700;line-height:1.15;margin:2px 0}
.archtag{font-size:13px;opacity:.9}
h2.sec{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:%(ACCENT)s;
  margin:34px 0 14px;border-bottom:2px solid %(ACCENT_SOFT)s;padding-bottom:8px;font-weight:700}
.card{background:#fff;border:1px solid %(GAP)s;border-radius:14px;padding:18px 20px;
  margin-bottom:14px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.rhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.dots{font-size:13px;letter-spacing:1px}
.rtype{font-size:11px;text-transform:uppercase;background:%(ACCENT_SOFT)s;color:%(ACCENT)s;
  padding:2px 8px;border-radius:6px;font-weight:600;letter-spacing:.5px}
.rname{font-size:17px;font-weight:700}
.rname a{color:%(ACCENT)s;text-decoration:none}
.rname a:hover{text-decoration:underline}
.rjob{color:%(MUTED)s;font-size:13px;margin-left:auto}
.ev{margin:6px 0}.desc{color:#374151;margin:6px 0 0}
details.rec-desc{margin:8px 0 0}
details.rec-desc>summary{cursor:pointer;list-style:none;outline:none;
  font-size:13px;font-weight:600;color:%(MUTED)s;
  padding:4px 0;user-select:none;
  display:inline-flex;align-items:center;gap:6px}
details.rec-desc>summary::-webkit-details-marker{display:none}
details.rec-desc>summary::before{content:"▸";font-size:11px;color:%(MUTED)s;
  transition:transform .15s}
details.rec-desc[open]>summary::before{transform:rotate(90deg)}
details.rec-desc>summary:hover{color:%(INK)s}
.install{margin-top:10px;display:flex;flex-direction:column;gap:6px}
code{font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:#0f172a;color:#e2e8f0;
  padding:7px 11px;border-radius:8px;display:block;overflow-x:auto}
.card.coach{border-left:4px solid %(ACCENT)s}
.coach h4{margin:0 0 12px;font-size:18px;font-weight:700;color:%(INK)s;
  padding-bottom:8px;border-bottom:1px solid %(GAP)s}
.coach p{margin:5px 0}
.tag{display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.5px;padding:1px 7px;border-radius:5px;margin-right:6px}
.ev-t{background:%(ACCENT_SOFT)s;color:%(ACCENT)s}.cost-t{background:#FEF2F2;color:%(BAD)s}
.saw-t{background:#F3F4F6;color:#374151}
.best-t{background:#ECFDF5;color:%(GOOD)s}
.hand-t{background:#EFF6FF;color:%(INFO)s}
ul.gaps{list-style:none;padding:0;margin:0}
ul.gaps li{background:#fff;border:1px solid %(GAP)s;border-left:3px solid %(MUTED)s;
  border-radius:10px;padding:12px 16px;margin-bottom:10px}
ul.gaps code{display:inline-block;margin-top:6px}
.charts{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start}
.chart{background:#fff;border:1px solid %(GAP)s;border-radius:14px;padding:16px 18px;flex:1;min-width:280px}
.chart h3{margin:0 0 12px;font-size:14px}
.bl{font-size:12px;fill:%(INK)s}.bv{font-size:12px;fill:%(MUTED)s}
.cap{font-size:12px;color:%(MUTED)s;margin:10px 0 0;text-align:center}
.scorecard{padding:6px 20px}
details.scrow{border-bottom:1px solid #f1f2f5}
details.scrow:last-child{border-bottom:0}
details.scrow summary{display:grid;
  grid-template-columns:18px minmax(0,1fr) 180px 90px 120px;
  align-items:center;gap:14px;padding:14px 0;cursor:pointer;
  list-style:none;outline:none}
details.scrow summary::-webkit-details-marker{display:none}
.scchev{color:%(MUTED)s;font-size:12px;transition:transform .15s;justify-self:start}
details.scrow[open] .scchev{transform:rotate(90deg)}
.scmain{min-width:0}
.sclabel{font-weight:600;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.scval{font-variant-numeric:tabular-nums;font-weight:700;font-size:15px;white-space:nowrap;color:%(MUTED)s;text-align:right}
.scspark{color:%(ACCENT)s;opacity:.85;text-align:right;line-height:0}
@media(max-width:680px){
  details.scrow summary{grid-template-columns:18px minmax(0,1fr) auto;row-gap:6px;column-gap:10px}
  .scval{grid-column:2/4;text-align:left}
  .scspark{display:none}
}
.delta{font-size:12px;font-weight:600;margin-left:6px}
/* legacy direction-only (kept for back-compat with payloads that don't declare direction) */
.delta.up{color:%(BAD)s}
.delta.down{color:%(GOOD)s}
/* direction-of-goodness (preferred) */
.delta.good{color:%(GOOD)s}
.delta.bad{color:%(BAD)s}
.sline.good{color:%(GOOD)s}
.sline.bad{color:%(BAD)s}
.scrow .verdict{margin:0;justify-self:end}
.scbody{padding:0 0 16px 24px;color:#374151;font-size:13.5px;line-height:1.55}
.scexplain{margin-top:8px;padding:10px 12px;background:#f7f8fb;border-radius:8px;color:%(INK)s}
.verdict{text-align:center;font-size:12.5px;font-weight:600;margin:12px auto 0;
  padding:5px 12px;border-radius:999px;display:block;width:fit-content}
.verdict.good{background:#ECFDF5;color:%(GOOD)s}
.verdict.watch{background:#FFFBEB;color:%(WATCH)s}
.verdict.bad{background:#FEF2F2;color:%(BAD)s}
.verdict.nodata{background:#F3F4F6;color:%(MUTED)s}
/* combined hero (archetype + verdict in one card) */
.hero-card{position:relative;background:linear-gradient(135deg,%(ACCENT)s,#7c3aed);color:#fff;
  border-radius:20px;padding:28px 32px;margin-bottom:20px;box-shadow:0 8px 28px rgba(109,40,217,.18)}
.hv-sub{font-size:12px;text-transform:uppercase;letter-spacing:.8px;opacity:.85;margin-bottom:10px}
.hero-arch-title{margin:0 0 4px;font-size:30px;font-weight:800;letter-spacing:-.3px;line-height:1.1}
.hero-arch-tag{margin:0 0 14px;font-size:14px;opacity:.9;font-style:italic;max-width:60ch}
.hero-verdict-row{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.hero-verdict-label{font-size:11px;text-transform:uppercase;letter-spacing:.8px;opacity:.85}
.hero-verdict-name{font-size:22px;font-weight:800;letter-spacing:-.2px}
.hero-summary{margin:0 0 12px;font-size:15.5px;line-height:1.45;opacity:.95;max-width:60ch}
.hv-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.chip{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);
  padding:4px 10px;border-radius:999px;font-size:12px;font-variant-numeric:tabular-nums}
/* Daimon chip inside the hero (overrides the archetype-card placement) */
.hero-card .daimon-chip{position:absolute;top:18px;right:20px;background:rgba(255,255,255,.18);
  color:#fff;border-color:rgba(255,255,255,.28)}
/* Expandable archetype details, kept calm */
.hero-details{margin-top:6px;background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.18);
  border-radius:12px;padding:6px 14px}
.hero-details summary{cursor:pointer;font-size:12.5px;font-weight:700;letter-spacing:.4px;
  text-transform:uppercase;opacity:.92;padding:8px 0;list-style:none}
.hero-details summary::-webkit-details-marker{display:none}
.hero-details summary:before{content:"▸ ";display:inline-block;margin-right:4px;transition:transform .15s}
.hero-details[open] summary:before{content:"▾ "}
.hero-details .arch-dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 14px;
  margin:6px 0 12px;font-size:13.5px;line-height:1.5}
.hero-details .arch-dt{font-weight:700;color:#FDE68A}
.hero-details .arch-dd{margin:0;color:#fff;opacity:.92}
@media(max-width:520px){.hero-card .daimon-chip{position:static;display:inline-flex;margin:0 0 12px}}
/* primary action */
.primary-action{border-left:5px solid %(ACCENT)s}
.pa-tag{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:%(ACCENT)s;
  font-weight:700;margin-bottom:6px}
.pa-title{margin:0 0 10px;font-size:22px;font-weight:700;color:%(INK)s}
.pa-phrase{display:inline-block;margin:0 0 12px}
.pa-why,.pa-source{margin:6px 0;color:#374151}
.pa-source{color:%(MUTED)s;font-size:13px}
/* archetype card */
.archetype-card .archlabel{font-size:11px;text-transform:uppercase;letter-spacing:.8px;
  color:%(MUTED)s;margin-bottom:4px}
.archetype-card .archtitle{font-size:22px;font-weight:700;color:%(INK)s;line-height:1.15}
.archetype-card .archtag{font-size:14px;color:#374151;margin-top:4px;font-style:italic}
.arch-dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 14px;margin:14px 0 0;
  font-size:14px;line-height:1.5}
.arch-dt{font-weight:700;color:%(ACCENT)s}
.arch-dd{margin:0;color:#374151}
/* trust ledger */
.trust .trust-title{font-size:15px;margin:0 0 8px;color:%(INK)s}
.trust-list{margin:0;padding:0 0 0 18px;color:#374151;font-size:13.5px;line-height:1.55}
.trust-list li{margin:4px 0}
/* daimon level chip (top-right of archetype card; absolute so it never
   collides with the archetype label on narrow screens) */
.archetype-card{position:relative}
.daimon-chip{position:absolute;top:14px;right:18px;display:inline-flex;align-items:center;gap:6px;
  background:%(ACCENT_SOFT)s;color:%(ACCENT)s;border:1px solid %(GAP)s;
  border-radius:999px;padding:4px 10px;font-size:12px;font-weight:600}
.daimon-chip .dc-val{font-size:14px;font-weight:800;margin:0 2px}
.daimon-chip .dc-xp{opacity:.75;font-weight:500}
@media(max-width:520px){.daimon-chip{position:static;display:inline-flex;margin:0 0 10px}}
/* quest card — single strong label, no duplicate pseudo-element */
.quest{border:2px solid #d7bc92;background:#fff8e7;border-left-width:6px;border-left-color:#B45309;position:relative}
.quest-tag{display:inline-block;background:#B45309;color:#fff;font-size:11px;text-transform:uppercase;
  letter-spacing:1px;font-weight:900;padding:4px 10px;border-radius:6px;margin-bottom:10px}
.quest-tag:before{content:"⚔ ";margin-right:2px}
.quest-title{margin:0 0 8px;font-size:22px;font-weight:900;color:#2F271F}
.quest-why{margin:6px 0;color:#374151}
.quest-do{margin:10px 0}
.quest-do-label{font-size:11px;font-weight:900;color:%(GOOD)s;text-transform:uppercase;letter-spacing:.5px}
.quest-do code{display:inline-block;margin-left:6px}
.quest-reward{margin:10px 0 0;color:#374151;font-size:13.5px;font-weight:700}
.quest-note{display:block;margin-top:4px;color:%(MUTED)s;font-size:12px;font-weight:500}
.quest-track{display:inline-block;margin:4px 0 8px;font-size:12px;color:%(MUTED)s}
.quest-empty{background:#fff;border-color:%(GAP)s;border-left-color:%(MUTED)s}
.quest-empty .quest-tag{background:%(MUTED)s}
.quest-jump{display:inline;color:#8A5B21;text-decoration:underline;text-underline-offset:2px;
  font-size:12.5px;font-weight:600;margin-left:6px}
.quest-jump:hover{color:#B45309}
.quest-empty .quest-jump{color:%(MUTED)s}
.quest-empty .quest-jump:hover{color:#4B5563}
html{scroll-behavior:smooth}
/* grove */
.grove-card{padding:0;overflow:hidden;background:#fffdf8;border-color:#e7d6b6}
.grove-banner{padding:18px 22px 16px;background:#fff4dc;border-bottom:1px solid #ead6b5}
.grove-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
.grove-kicker{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:#8A5B21;font-weight:800}
.grove-title{margin:2px 0 0;font-size:24px;font-weight:900;color:#2F271F;letter-spacing:0}
.grove-lead{margin:6px 0 0;color:#6B5A46;max-width:68ch}
.grove-stats{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.grove-stat{min-width:118px;background:#fffaf0;border:1px solid #ead6b5;border-radius:8px;padding:8px 10px;text-align:right}
.grove-stat b{display:block;font-size:18px;line-height:1;color:#2F271F;font-variant-numeric:tabular-nums}
.grove-stat span{display:block;margin-top:4px;font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#7B6A58}
.grove-progress{margin-top:12px;background:#ead6b5;border-radius:999px;height:12px;overflow:hidden;border:1px solid #d7bc92}
.grove-progress span{display:block;height:100%%;min-width:8px;background:#0F766E;border-radius:999px}
.grove-guide{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.grove-guide-pill{background:#fffaf0;border:1px solid #ead6b5;border-radius:8px;padding:7px 9px;
  color:#4B5563;font-size:12px;line-height:1.2}
.grove-guide-pill b{color:#2F271F}
.grove-body{padding:16px 18px 18px}
.grove-summary{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:14px;align-items:start;margin-bottom:14px}
.grove-summary-main{font-size:16px;color:#374151}
.grove-summary-main b{color:%(GOOD)s}
.grove-next{margin:6px 0 0;color:#4B5563;font-size:13.5px}
.grove-relics{display:flex;flex-wrap:wrap;gap:7px;justify-content:flex-end;max-width:360px}
.grove-map{background:#fff8e7;border:1px solid #ead6b5;border-radius:8px;padding:8px;margin-bottom:14px}
.grove-map svg{display:block;width:100%%;height:auto}
.grove-skills{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:12px}
.grove-skill{border:1px solid #ead6b5;border-radius:8px;background:#fff;padding:12px;min-height:132px}
.grove-skill.changed{border-color:#A7F3D0;background:#F0FDF4}
.grove-skill.needs{border-color:#FDE68A;background:#FFFBEB}
.grove-skill-head{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.grove-sigil{width:34px;height:34px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;
  background:#F3E8FF;color:%(ACCENT)s;font-size:12px;font-weight:900;border:1px solid #DDD6FE}
.grove-skill.changed .grove-sigil{background:#D1FAE5;color:%(GOOD)s;border-color:#A7F3D0}
.grove-skill.needs .grove-sigil{background:#FEF3C7;color:%(WATCH)s;border-color:#FDE68A}
.grove-skill-title{font-weight:800;color:#2F271F;line-height:1.15}
.grove-skill-meta{font-size:12px;color:%(MUTED)s;font-variant-numeric:tabular-nums}
.grove-skill-delta{display:inline-block;margin-bottom:7px;font-size:12px;font-weight:800;border-radius:999px;padding:3px 8px;background:#F3F4F6;color:%(MUTED)s}
.grove-skill-delta.changed{background:#D1FAE5;color:%(GOOD)s}
.grove-skill-delta.needs{background:#FEF3C7;color:%(WATCH)s}
.grove-skill-evidence{font-size:12.5px;color:#4B5563;line-height:1.4}
.grove-ledger{border-top:1px solid #ead6b5;padding-top:10px}
.grove-ledger summary{cursor:pointer;color:#6B5A46;font-weight:800;font-size:13px;list-style:none}
.grove-ledger summary::-webkit-details-marker{display:none}
.grove-ledger summary:before{content:"▸";display:inline-block;margin-right:6px;color:%(MUTED)s}
.grove-ledger[open] summary:before{transform:rotate(90deg)}
.grove-tracks{width:100%%;border-collapse:collapse;font-size:13px;margin:10px 0 6px}
.grove-tracks th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.5px;
  color:%(MUTED)s;border-bottom:1px solid %(GAP)s;padding:6px 8px}
.grove-tracks td{padding:7px 8px;border-bottom:1px solid #f1f2f5}
.grove-tracks td.num,.grove-tracks th.num{text-align:right;font-variant-numeric:tabular-nums}
.grove-ev{color:%(MUTED)s;font-size:13px}
.delta-flat{color:%(MUTED)s}
.grove-badge{font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid %(GAP)s}
.grove-badge.on{background:#ECFDF5;border-color:#A7F3D0;color:%(GOOD)s;font-weight:700}
.grove-badge.off{color:%(MUTED)s;background:#fff}
.grove-cap{margin:10px 0 0;color:%(MUTED)s;font-size:12px;text-align:center}
@media(max-width:760px){.grove-summary{grid-template-columns:1fr}.grove-relics{justify-content:flex-start}.grove-skills{grid-template-columns:1fr}.grove-stat{text-align:left}}
/* catalog source strip (outside the hero now) */
.srcstrip{padding:10px 14px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.srclabel-dark{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;
  color:%(MUTED)s}
.srclabel-dark span{font-weight:400;text-transform:none;letter-spacing:0}
.badge-dark{background:%(ACCENT_SOFT)s;border:1px solid %(GAP)s;color:%(ACCENT)s;
  padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600}
.seclead{color:%(MUTED)s;font-size:14px;margin:-6px 0 12px}
h2.sec.big{font-size:20px;text-transform:none;letter-spacing:0;color:%(INK)s;border:0;
  margin:8px 0 16px}
.coachbreak{height:3px;margin:40px 0 0;border-radius:2px;
  background:linear-gradient(90deg,%(ACCENT)s,#7c3aed)}
.softbreak{height:1px;margin:30px 0 0;background:%(GAP)s}
.recap .mixbar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:%(GAP)s}
.recap .seg{height:100%%;display:inline-block}
.recap .seg.k-dev{background:#6366f1}.recap .seg.k-writing{background:#10b981}
.recap .seg.k-data{background:#f59e0b}.recap .seg.k-ops{background:#ec4899}
.recap .seg.k-other{background:#9ca3af}
table.rt{width:100%%;border-collapse:collapse;margin-top:14px;font-size:14px}
table.rt th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.5px;
  color:%(MUTED)s;border-bottom:1px solid %(GAP)s;padding:6px 8px}
table.rt td{padding:7px 8px;border-bottom:1px solid #f1f2f5}
table.rt td.num,table.rt th.num{text-align:right;font-variant-numeric:tabular-nums}
.kind{font-size:11px;font-weight:600;padding:2px 8px;border-radius:6px;text-transform:uppercase}
.kind.k-dev{background:#eef2ff;color:#4f46e5}.kind.k-writing{background:#ecfdf5;color:#059669}
.kind.k-data{background:#fffbeb;color:#d97706}.kind.k-ops{background:#fdf2f8;color:#db2777}
.kind.k-other{background:#f3f4f6;color:#6b7280}
footer{margin-top:40px;color:%(MUTED)s;font-size:12px;text-align:center}
/* embedded /insights sections — minimal styles lifted from Anthropic's report
   so the patterns + horizon snippets render coherently inside our wrap. */
.insights-attrib{font-size:11px;color:%(MUTED)s;text-transform:uppercase;letter-spacing:1px;
  margin:8px 0 14px;padding:6px 10px;background:#FDECC8;border-radius:6px;display:inline-block}
.patterns-section,.horizon-section{display:flex;flex-direction:column;gap:12px;margin:8px 0 16px}
.pattern-card{background:#f0f9ff;border:1px solid #7dd3fc;border-radius:8px;padding:16px}
.pattern-title{font-weight:600;font-size:15px;color:#0f172a;margin-bottom:6px}
.pattern-summary{font-size:14px;color:#475569;margin-bottom:8px}
.pattern-detail{font-size:13px;color:#334155;line-height:1.5}
.copyable-prompt-section{margin-top:12px;padding-top:12px;border-top:1px solid #e2e8f0}
.copyable-prompt-row{display:flex;align-items:flex-start;gap:8px}
.copyable-prompt{flex:1;background:#f8fafc;padding:10px 12px;border-radius:4px;
  font-family:ui-monospace,monospace;font-size:12px;color:#334155;border:1px solid #e2e8f0;
  white-space:pre-wrap;line-height:1.5}
.prompt-label{font-size:11px;font-weight:600;text-transform:uppercase;color:#64748b;margin-bottom:6px}
.pattern-prompt{background:#f8fafc;padding:12px;border-radius:6px;margin-top:12px;border:1px solid #e2e8f0}
.pattern-prompt code{font-family:ui-monospace,monospace;font-size:12px;color:#334155;
  display:block;white-space:pre-wrap;margin-bottom:8px}
.horizon-card{background:linear-gradient(135deg,#faf5ff 0%%,#f5f3ff 100%%);
  border:1px solid #c4b5fd;border-radius:8px;padding:16px}
.horizon-title{font-weight:600;font-size:15px;color:#5b21b6;margin-bottom:8px}
.horizon-possible{font-size:14px;color:#334155;margin-bottom:10px;line-height:1.5}
.horizon-tip{font-size:13px;color:#6b21a8;background:rgba(255,255,255,0.6);
  padding:8px 12px;border-radius:4px;margin-bottom:10px}
""" % {
    "INK": INK, "ACCENT": ACCENT, "ACCENT_SOFT": ACCENT_SOFT,
    "MUTED": MUTED, "GAP": GAP, "BG": BG,
    "GOOD": GOOD, "WATCH": WATCH, "BAD": BAD, "INFO": INFO,
}


# Inline JS: highlight the sidebar entry whose <section> is currently in
# the upper-mid third of the viewport. No-op when there is no sidebar.
ACTIVE_SECTION_JS = """
<script>
(function(){
  var navs = Array.from(document.querySelectorAll('.snav'));
  if (!navs.length || !('IntersectionObserver' in window)) return;
  var byEl = new Map();
  var ordered = [];
  navs.forEach(function(a){
    var id = (a.getAttribute('href') || '').slice(1);
    var el = id && document.getElementById(id);
    if (el) { byEl.set(el, a); ordered.push(el); }
  });
  if (!byEl.size) return;
  var visible = new Set();
  function pick(){
    for (var i = 0; i < ordered.length; i++) {
      var el = ordered[i];
      if (visible.has(el)) {
        navs.forEach(function(a){ a.classList.remove('active'); a.removeAttribute('aria-current'); });
        var hit = byEl.get(el);
        hit.classList.add('active');
        hit.setAttribute('aria-current', 'true');
        return;
      }
    }
  }
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if (e.isIntersecting) visible.add(e.target);
      else visible.delete(e.target);
    });
    pick();
  }, { rootMargin: '-20% 0px -60% 0px', threshold: 0 });
  ordered.forEach(function(el){ io.observe(el); });
  // Initial pass — first section active until scroll fires the observer.
  navs[0].classList.add('active');
  navs[0].setAttribute('aria-current', 'true');
})();
</script>
"""


# Simplified-first view: extra styles + the Simple/Advanced toggle script.
SIMPLE_CSS = """
.simpleview{max-width:760px;margin:0 auto;padding:30px 20px 64px}
.simple-top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.simple-title{font-size:20px;font-weight:700;color:#171717}
.simple-title span{color:#6B7280;font-weight:400;font-size:15px}
.simple-arch-title{font-size:30px;font-weight:800;color:#171717;margin:4px 0 20px;letter-spacing:-0.02em}
.simple-arch-label{font-size:15px;font-weight:600;color:#6B7280;letter-spacing:0;display:block;margin-bottom:2px}
.viewtoggle{background:#6D28D9;color:#fff;border:0;border-radius:999px;padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.viewtoggle:hover{background:#5b21b6}
.simpleview .card{margin:0 0 14px}
.simpleview .sec{font-size:16px;margin:22px 0 10px}
.simple-pa{border-left:4px solid #6D28D9}
.simple-pa .pa-why{color:#374151;margin-top:8px}
.simple-rec .srhead{display:flex;align-items:center;gap:8px;margin-bottom:2px;flex-wrap:wrap}
.simple-rec .srline{color:#374151;font-size:14px;margin:4px 0 8px}
.simple-rec .srinstall{display:block;background:#F3F4F6;padding:8px 10px;border-radius:6px;font-size:12.5px;color:#334155;overflow-x:auto}
.simple-coach h4{margin:0 0 6px}
.simple-coach .scwhat{color:#374151;margin:0 0 4px}
.simple-coach .scwhy{color:#374151;margin:0 0 4px}
.simple-coach .scwhat b,.simple-coach .scwhy b,.simple-coach .scbetter b{color:#171717}
.simple-coach .scbetter{color:#171717;margin:0 0 4px}
.simple-coach .scsaw{color:#6B7280;font-size:13px;margin:0}
.simple-token{border-left:3px solid #0F766E}
.simple-note{color:#6B7280;font-size:13px;margin:0 0 10px}
.simple-foot{text-align:center;color:#9CA3AF;font-size:12px;margin-top:26px}
#view-advanced .viewbar{margin:0 0 16px}
/* Colorful archetype card in the simple view (matches the advanced hero) */
.simpleview .archetype-card{background:linear-gradient(135deg,#6D28D9,#7c3aed);color:#fff;border:0;padding:22px}
.simpleview .archetype-card .archlabel{color:#fff;opacity:.85}
.simpleview .archetype-card .archtitle{color:#fff;font-size:30px;font-weight:800;letter-spacing:-.3px}
.simpleview .archetype-card .archtag{color:#fff;opacity:.9}
.simpleview .archetype-card .arch-dt{color:#FDE68A}
.simpleview .archetype-card .arch-dd{color:#fff;opacity:.92}
"""

VIEW_TOGGLE_JS = """
<script>
function sdToggleView(){
  var s=document.getElementById('view-simple'),a=document.getElementById('view-advanced');
  if(!s||!a)return;
  var advHidden=getComputedStyle(a).display==='none';
  a.style.display=advHidden?'block':'none';
  s.style.display=advHidden?'none':'block';
  window.scrollTo(0,0);
}
</script>
"""

# Share card: a button to download the wrapped archetype card as a self-contained
# SVG. Fully client-side — builds a Blob from the embedded SVG and downloads it.
# Nothing is uploaded; the user posts the file themselves.
SHARE_CSS = """
.viewtoggle.share{background:#6D28D9}
.viewtoggle.share:hover{background:#5b21b6}
.share-preview{margin:0 0 14px}
.share-preview summary{cursor:pointer;color:#6D28D9;font-size:13px;font-weight:600}
.share-preview svg{max-width:340px;width:100%;height:auto;margin-top:10px;border:1px solid #E5E7EB;border-radius:12px;box-shadow:0 6px 24px rgba(76,29,149,.18)}
.share-sizes{margin:8px 0 0;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.share-sizes label{color:#6B7280;font-size:12px}
.share-sizes select{font-size:12px;padding:2px 6px;border:1px solid #E5E7EB;border-radius:6px}
.share-hint{color:#9CA3AF;font-size:12px;margin:6px 0 0}
.simple-actions{display:flex;gap:8px;flex-wrap:wrap}
"""

# Client-side share: rasterize the selected SVG card to PNG (platforms reject
# SVG), then offer the native share sheet with a pre-filled caption, falling
# back to a PNG download + X intent on desktop. Nothing is uploaded.
SHARE_JS = """
<script>
function sdPickSvg(){
  var sel=document.getElementById('sd-size'); var size=sel?sel.value:'square';
  var t=document.getElementById('sd-card-'+size)||document.getElementById('sd-card-square');
  return t?t.innerHTML.trim():'';
}
function sdSwapPreview(){
  var box=document.getElementById('sd-preview'); if(!box)return;
  box.innerHTML=sdPickSvg();
}
function sdSvgToPng(svg){
  return new Promise(function(resolve,reject){
    var m=svg.match(/viewBox=\\"0 0 (\\d+) (\\d+)\\"/);
    var w=m?+m[1]:1080, h=m?+m[2]:1080;
    var blob=new Blob([svg],{type:'image/svg+xml;charset=utf-8'});
    var url=URL.createObjectURL(blob), img=new Image();
    img.onload=function(){
      var c=document.createElement('canvas'); c.width=w; c.height=h;
      c.getContext('2d').drawImage(img,0,0,w,h);
      URL.revokeObjectURL(url);
      c.toBlob(function(png){ png?resolve(png):reject(new Error('toBlob failed')); },'image/png');
    };
    img.onerror=function(e){ URL.revokeObjectURL(url); reject(e); };
    img.src=url;
  });
}
function sdDownloadPng(png){
  var url=URL.createObjectURL(png), a=document.createElement('a');
  a.href=url; a.download='skills-daimon-archetype.png';
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}
function sdDownloadCard(){
  var svg=sdPickSvg(); if(!svg)return;
  sdSvgToPng(svg).then(sdDownloadPng).catch(function(){
    var b=new Blob([svg],{type:'image/svg+xml'}),u=URL.createObjectURL(b),a=document.createElement('a');
    a.href=u; a.download='skills-daimon-archetype.svg'; document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(u);
  });
}
function sdShareCard(){
  var svg=sdPickSvg(); if(!svg)return;
  var cap=(window.__sdShare&&window.__sdShare.caption)||'My Claude Code archetype';
  sdSvgToPng(svg).then(function(png){
    // Always land the file on disk — that's the reliable cross-browser path.
    sdDownloadPng(png);
    // On devices with native file share (mostly mobile), also open the share
    // sheet with the caption ready. Best-effort; never blocks the download.
    var file=new File([png],'skills-daimon-archetype.png',{type:'image/png'});
    if(navigator.canShare&&navigator.canShare({files:[file]})){
      navigator.share({files:[file],text:cap,title:'Skills Daimon'}).catch(function(){});
    }
  }).catch(function(){ sdDownloadCard(); });
}
</script>
"""


def _gamify_blocks(payload: dict, m: dict):
    """Return (level_chip_html, quest_card_html, grove_section_html, game_state)
    — empty strings + None if gamification is unavailable. Pulls fresh history
    so deltas are honest. Never raises into the renderer. The game_state is
    returned so the caller can build the share card from the same numbers."""
    try:
        import gamify as _gamify    # type: ignore
        import history as _history  # type: ignore
    except Exception:
        return "", "", "", None
    try:
        entries = _history.read_last(8, window_days=int(m.get("days") or 0) or None)
    except Exception:
        entries = []
    try:
        gs = _gamify.build_game_state(payload, entries, today=m.get("date"),
                                       window_days=m.get("days"))
    except Exception:
        return "", "", "", None

    # Top: a compact Daimon Level chip next to the archetype.
    level_chip = (
        f'<div class="daimon-chip" title="Daimon Level">'
        f'<span class="dc-label">Daimon Level</span>'
        f'<span class="dc-val">{int(gs["daimon_level"])}</span>'
        f'<span class="dc-xp">· {int(gs["xp_total"])} XP</span>'
        f'</div>'
    )

    summary = gs.get("grove_summary") or _gamify.build_grove_summary(gs)

    # Quest card (after compact Grove summary).
    quest = gs.get("active_quest")
    if quest:
        quest_html = (
            '<section class="quest card">'
            '<div class="quest-tag">Active mission</div>'
            f'<h2 class="quest-title">{esc(quest.get("title"))}</h2>'
            f'<span class="quest-track">Track: {esc(_gamify.display_track_name(quest.get("track","")))}</span>'
            f'<p class="quest-why">{esc(quest.get("why",""))}</p>'
            f'<div class="quest-do"><span class="quest-do-label">Do</span>'
            f' <code>{esc(quest.get("do",""))}</code></div>'
            f'<p class="quest-reward"><b>Reward.</b> {esc(quest.get("reward",""))} '
            '<a class="quest-jump" href="#daimon-grove">see stats ↓</a>'
            '<span class="quest-note">No XP for accepting the mission; XP unlocks only after a later report verifies the improvement.</span></p>'
            '</section>'
        )
    else:
        quest_html = (
            '<section class="quest quest-empty card">'
            '<div class="quest-tag">Mission board</div>'
            '<h2 class="quest-title">No mission this run</h2>'
            '<p class="quest-why">No strong evidence-backed mission appeared in this report. '
            '<a class="quest-jump" href="#daimon-grove">see stats ↓</a></p>'
            '</section>'
        )

    # Grove + XP movements.
    earned = sum(1 for b in gs.get("badges", []) if b.get("earned"))
    delta_total = int(summary.get("xp_delta_total") or 0)
    xp_total = int(gs.get("xp_total") or 0)
    daimon_level = int(gs.get("daimon_level") or 0)
    thresholds = getattr(_gamify, "DAIMON_LEVEL_THRESHOLDS", (0, 100))
    floor_idx = max(0, min(daimon_level - 1, len(thresholds) - 1))
    next_idx = max(0, min(daimon_level, len(thresholds) - 1))
    level_floor = int(thresholds[floor_idx])
    next_level_xp = int(thresholds[next_idx])
    if next_level_xp <= level_floor:
        progress_pct = 100
        progress_label = "Max rank reached"
    else:
        progress_pct = max(0, min(100, round(100 * (xp_total - level_floor) / (next_level_xp - level_floor))))
        progress_label = f'{max(0, next_level_xp - xp_total)} XP to Level {daimon_level + 1}'

    track_rows = []
    skill_cards = []
    sigils = {
        "automation": "CT",
        "memory": "MW",
        "safety": "GT",
        "planning": "PP",
        "tool_fluency": "TS",
        "project_hygiene": "RS",
    }
    for track_id in _gamify.TRACKS:
        t = gs["tracks"].get(track_id) or {}
        delta = int(t.get("delta") or 0)
        status = t.get("status") or ""
        delta_cls = "delta down" if delta > 0 else "delta-flat"
        card_cls = "grove-skill"
        delta_badge_cls = "grove-skill-delta"
        if delta > 0:
            card_cls += " changed"
            delta_badge_cls += " changed"
        elif status == "needs_data":
            card_cls += " needs"
            delta_badge_cls += " needs"
        delta_label = esc(t.get("delta_label") or _gamify.format_delta(delta, status))
        skill_cards.append(
            f'<article class="{card_cls}">'
            '<div class="grove-skill-head">'
            f'<span class="grove-sigil">{esc(sigils.get(track_id, ""))}</span>'
            '<div>'
            f'<div class="grove-skill-title">{esc(t.get("grove_area") or t.get("name") or _gamify.display_track_name(track_id))}</div>'
            f'<div class="grove-skill-meta">{esc(t.get("name") or _gamify.display_track_name(track_id))} · L{int(t.get("level") or 0)} · {int(t.get("xp") or 0)} XP</div>'
            '</div></div>'
            f'<span class="{delta_badge_cls}">{delta_label}</span>'
            f'<div class="grove-skill-evidence">{esc(t.get("evidence",""))}</div>'
            '</article>'
        )
        track_rows.append(
            f'<tr><td>{esc(t.get("name") or _gamify.display_track_name(track_id))}</td>'
            f'<td class="num">L{int(t.get("level") or 0)}</td>'
            f'<td class="num">{int(t.get("xp") or 0)} XP</td>'
            f'<td class="num"><span class="{delta_cls}">{delta_label}</span></td>'
            f'<td class="grove-ev">{esc(t.get("evidence",""))}</td></tr>'
        )
    earned_badges = [b for b in gs.get("badges", []) if b.get("earned")]
    badges_html = "".join(
        f'<span class="grove-badge on">{esc(b["name"])}</span>'
        for b in earned_badges
    )
    if not badges_html:
        badges_html = '<span class="grove-badge off">No badges earned yet. Badges unlock from verified milestones.</span>'
    rhythm_count = len(gs.get("rhythms") or {})
    grove_html = (
        '<div class="card grove-card" id="daimon-grove">'
        '<div class="grove-banner">'
        '<div class="grove-head">'
        '<div>'
        '<div class="grove-kicker">RPG habit map</div>'
        f'<h2 class="grove-title">Daimon Grove · Level {daimon_level}</h2>'
        '<p class="grove-lead">Each landmark is one craft habit. Green means it improved this run; amber means the report needs more evidence.</p>'
        '</div>'
        '<div class="grove-stats">'
        f'<div class="grove-stat"><b>{xp_total}</b><span>Total XP</span></div>'
        f'<div class="grove-stat"><b>+{delta_total}</b><span>XP earned this run</span></div>'
        f'<div class="grove-stat"><b>{earned}</b><span>Relics</span></div>'
        '</div>'
        '</div>'
        f'<div class="grove-progress" aria-label="{esc(progress_label)}"><span style="width:{progress_pct}%"></span></div>'
        f'<p class="cap">{esc(progress_label)}</p>'
        '<div class="grove-guide" aria-label="Daimon Grove legend">'
        '<span class="grove-guide-pill"><b>Landmark</b> habit area</span>'
        '<span class="grove-guide-pill"><b>Green</b> verified improvement</span>'
        '<span class="grove-guide-pill"><b>Amber</b> needs evidence</span>'
        '<span class="grove-guide-pill"><b>Relic</b> verified milestone</span>'
        '<span class="grove-guide-pill"><b>Mission</b> next habit to try</span>'
        '</div>'
        '</div>'
        '<div class="grove-body">'
        '<div class="grove-summary">'
        '<div class="grove-summary-main">'
        f'<b>+{delta_total} XP verified this run.</b> {esc(summary.get("change_sentence",""))}'
        f'<p class="grove-next">{esc(summary.get("next_quest",""))}</p>'
        '</div>'
        f'<div class="grove-relics">{badges_html}</div>'
        '</div>'
        f'<div class="grove-map">{_gamify.render_grove_svg(gs["grove"])}</div>'
        f'<div class="grove-skills">{"".join(skill_cards)}</div>'
        '<details class="grove-ledger">'
        '<summary>Why XP changed (evidence ledger)</summary>'
        '<table class="grove-tracks">'
        '<thead><tr><th>Track</th><th class="num">Level</th><th class="num">XP</th>'
        '<th class="num">This run</th><th>Evidence receipt</th></tr></thead>'
        f'<tbody>{"".join(track_rows)}</tbody></table>'
        '</details>'
        f'<p class="grove-cap">Relics earned: {earned} · Missions verified: {int(gs.get("quests_completed_count",0))} · '
        f'Privacy: {rhythm_count} numeric signals saved, no commands, paths, or session IDs</p>'
        '</div>'
        '</div>'
    )

    return level_chip, quest_html, grove_html, gs


# Share-card palette — one bold, branded look that survives a screenshot feed.
SD_CARD_FONT = "-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"
SD_BG_STOPS = (("#4C1D95", 0), ("#6D28D9", 55), ("#7C3AED", 100))   # indigo→violet
SD_HERO_STOPS = (("#F0ABFC", 0), ("#FDE68A", 100))                  # pink→amber
SD_INK = "#F5F3FF"        # high-contrast light ink on the dark card
SD_INK_SOFT = "#C4B5FD"   # muted lavender for labels


def share_pill_text(level: int, badge_count: int, balanced: bool) -> str:
    """Honest achievement tier from real game state — never a fabricated
    percentile. Balanced Grove (all six tracks ≥ L2) is the headline when earned;
    otherwise Daimon Level + earned badge count."""
    level = _int(level)
    if balanced:
        return f"🌌 Balanced Grove · Level {level}"
    badge_count = _int(badge_count)
    badges = "badge" if badge_count == 1 else "badges"
    return f"✨ Daimon Level {level} · {badge_count} {badges}"


def _top_mix(work_mix: dict, n: int = 2) -> str:
    """Top-n work-mix tracks as a bold dual-stat, e.g. '46% dev · 42% writing'."""
    mix = sorted(((k, _int(v)) for k, v in (work_mix or {}).items() if _int(v) > 0),
                 key=lambda kv: -kv[1])[:n]
    return " · ".join(f"{v}% {_svg_text(k)}" for k, v in mix)


def _grad(gid: str, stops, vertical: bool = True) -> str:
    coords = 'x1="0" y1="0" x2="0" y2="1"' if vertical else 'x1="0" y1="0" x2="1" y2="0"'
    body = "".join(f'<stop offset="{off}%" stop-color="{col}"/>' for col, off in stops)
    return f'<linearGradient id="{gid}" {coords}>{body}</linearGradient>'


# Per-size geometry. Vertical sizes (square / story) carry an explicit y-map so
# the oversized hero never collides with the brand chip or identity block; the
# link size is horizontal and handled separately. Coordinates are baselines.
SD_SIZES = {
    "square": {
        "w": 1080, "h": 1080, "horizontal": False,
        "brand": 120, "hero": 400, "hero_fs": 220, "sess": 470, "days": 515,
        "youre": 610, "title": 686, "title_fs": 64, "tag": 736, "mix": 826,
        "pill": 896, "foot": 1024,
    },
    "story": {
        "w": 1080, "h": 1920, "horizontal": False,
        "brand": 250, "hero": 760, "hero_fs": 300, "sess": 850, "days": 902,
        "youre": 1040, "title": 1126, "title_fs": 76, "tag": 1182, "mix": 1300,
        "pill": 1380, "foot": 1840,
    },
    "link": {"w": 1200, "h": 630, "horizontal": True},
}


def _intro_label(title: str) -> str:
    """'You're a' reads wrong when the archetype already carries an article
    ('The Builder-Scribe' → 'You're a The …'). Drop the article in that case."""
    first = title.strip().split(" ", 1)[0].lower()
    return "You’re" if first in {"the", "a", "an"} else "You’re a"


def _pill_width(pill: str, per_char: int, pad: int, floor: int) -> int:
    """Snug pill — the old generous per-char estimate ballooned the capsule."""
    return max(floor, per_char * len(pill) + pad)


def _card_vertical(cfg: dict, sid: str, sessions: str, days: int, title: str,
                   tagline: str, mix_txt: str, pill: str) -> str:
    """Hero-number identity card, vertical (square / story). One focal point:
    the oversized session count in gradient numerals."""
    w, h = cfg["w"], cfg["h"]
    cx = w // 2
    intro = _intro_label(title)
    pill_w = _pill_width(pill, 15, 64, 300)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" font-family="{SD_CARD_FONT}">'
        f'<defs>{_grad(f"sdbg-{sid}", SD_BG_STOPS)}'
        f'{_grad(f"sdhero-{sid}", SD_HERO_STOPS, vertical=False)}</defs>'
        f'<rect width="{w}" height="{h}" fill="url(#sdbg-{sid})"/>'
        f'<text x="{cx}" y="{cfg["brand"]}" text-anchor="middle" font-size="34" '
        f'font-weight="800" letter-spacing="3" fill="{SD_INK}">🏛 SKILLS DAIMON</text>'
        # Hero
        f'<text x="{cx}" y="{cfg["hero"]}" text-anchor="middle" font-size="{cfg["hero_fs"]}" '
        f'font-weight="800" fill="url(#sdhero-{sid})" letter-spacing="-4">{sessions}</text>'
        f'<text x="{cx}" y="{cfg["sess"]}" text-anchor="middle" font-size="46" '
        f'font-weight="800" letter-spacing="8" fill="{SD_INK}">SESSIONS</text>'
        f'<text x="{cx}" y="{cfg["days"]}" text-anchor="middle" font-size="28" '
        f'fill="{SD_INK_SOFT}">in {_int(days)} days</text>'
        # Identity
        f'<text x="{cx}" y="{cfg["youre"]}" text-anchor="middle" font-size="30" '
        f'fill="{SD_INK_SOFT}">{intro}</text>'
        f'<text x="{cx}" y="{cfg["title"]}" text-anchor="middle" font-size="{cfg["title_fs"]}" '
        f'font-weight="800" fill="{SD_INK}">{title}</text>'
        f'<text x="{cx}" y="{cfg["tag"]}" text-anchor="middle" font-size="30" '
        f'font-style="italic" fill="{SD_INK_SOFT}">{tagline}</text>'
        f'<text x="{cx}" y="{cfg["mix"]}" text-anchor="middle" font-size="40" '
        f'font-weight="700" fill="{SD_INK}">{mix_txt}</text>'
        # Honest achievement pill
        f'<rect x="{cx - pill_w // 2}" y="{cfg["pill"] - 38}" width="{pill_w}" height="56" '
        f'rx="28" fill="#FFFFFF" fill-opacity="0.14"/>'
        f'<text x="{cx}" y="{cfg["pill"]}" text-anchor="middle" font-size="26" '
        f'font-weight="700" fill="{SD_INK}">{pill}</text>'
        f'<text x="{cx}" y="{cfg["foot"]}" text-anchor="middle" font-size="20" '
        f'fill="{SD_INK_SOFT}" fill-opacity="0.8">Generated locally from my own '
        f'Claude Code usage · skills-daimon</text>'
        '</svg>'
    )


def _card_horizontal(cfg: dict, sid: str, sessions: str, days: int, title: str,
                     tagline: str, mix_txt: str, pill: str) -> str:
    """Link / OG variant (1200×630): hero left, identity right. The right column
    is anchored at its own center so long archetype names stay inside the card."""
    w, h = cfg["w"], cfg["h"]
    lx = 300            # left column center (hero)
    rcx = 850           # right column center (identity) — 500px-wide column
    intro = _intro_label(title)
    pill_w = _pill_width(pill, 14, 56, 280)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" font-family="{SD_CARD_FONT}">'
        f'<defs>{_grad(f"sdbg-{sid}", SD_BG_STOPS)}'
        f'{_grad(f"sdhero-{sid}", SD_HERO_STOPS, vertical=False)}</defs>'
        f'<rect width="{w}" height="{h}" fill="url(#sdbg-{sid})"/>'
        f'<text x="60" y="78" font-size="26" font-weight="800" letter-spacing="3" '
        f'fill="{SD_INK}">🏛 SKILLS DAIMON</text>'
        # Hero (left)
        f'<text x="{lx}" y="340" text-anchor="middle" font-size="190" font-weight="800" '
        f'fill="url(#sdhero-{sid})" letter-spacing="-4">{sessions}</text>'
        f'<text x="{lx}" y="402" text-anchor="middle" font-size="38" font-weight="800" '
        f'letter-spacing="6" fill="{SD_INK}">SESSIONS</text>'
        f'<text x="{lx}" y="444" text-anchor="middle" font-size="24" '
        f'fill="{SD_INK_SOFT}">in {_int(days)} days</text>'
        f'<line x1="585" y1="170" x2="585" y2="470" stroke="{SD_INK}" stroke-opacity="0.2"/>'
        # Identity (right, center-anchored)
        f'<text x="{rcx}" y="220" text-anchor="middle" font-size="26" '
        f'fill="{SD_INK_SOFT}">{intro}</text>'
        f'<text x="{rcx}" y="280" text-anchor="middle" font-size="50" font-weight="800" '
        f'fill="{SD_INK}">{title}</text>'
        f'<text x="{rcx}" y="324" text-anchor="middle" font-size="25" font-style="italic" '
        f'fill="{SD_INK_SOFT}">{tagline}</text>'
        f'<text x="{rcx}" y="390" text-anchor="middle" font-size="33" font-weight="700" '
        f'fill="{SD_INK}">{mix_txt}</text>'
        f'<rect x="{rcx - pill_w // 2}" y="420" width="{pill_w}" height="52" rx="26" '
        f'fill="#FFFFFF" fill-opacity="0.14"/>'
        f'<text x="{rcx}" y="453" text-anchor="middle" font-size="24" '
        f'font-weight="700" fill="{SD_INK}">{pill}</text>'
        f'<text x="{rcx}" y="555" text-anchor="middle" font-size="18" fill="{SD_INK_SOFT}" '
        f'fill-opacity="0.8">Generated locally · skills-daimon</text>'
        '</svg>'
    )


def build_share_card_svg(archetype: dict, work_mix: dict, sessions: int, days: int,
                         level: int, badge_count: int, balanced: bool,
                         size: str = "square") -> str:
    """A self-contained, bold 'wrapped' poster — non-identifying only.

    Hero-number identity card: an oversized session count as the single focal
    point, the archetype as identity, the top work-mix split, and an honest
    achievement pill (Daimon Level + badges, or Balanced Grove). `size` selects
    `square` (1080×1080), `story` (1080×1920), or `link` (1200×630).

    Deliberately excludes every identifying datum: project names/paths,
    per-project session/commit counts, recommendations, and coaching text. The
    aggregate session total is shown by explicit user opt-in. Pure inline SVG,
    no external assets."""
    cfg = SD_SIZES.get(size) or SD_SIZES["square"]
    sid = size if size in SD_SIZES else "square"
    archetype = archetype or {}
    title = _svg_text(archetype.get("title", "Your archetype"))
    tagline = _svg_text(archetype.get("tagline", ""))
    sessions_txt = _svg_text(f"{_int(sessions):,}")
    mix_txt = _top_mix(work_mix) or _svg_text("a mix of work")
    pill = _svg_text(share_pill_text(level, badge_count, balanced))
    builder = _card_horizontal if size == "link" else _card_vertical
    return builder(cfg, sid, sessions_txt, days, title, tagline, mix_txt, pill)


def simple_primary_action_card(a: dict) -> str:
    """Stripped-down primary action for the simple view (no Source line)."""
    if not a or not a.get("title"):
        return ""
    phrase = (a.get("phrase") or "").strip()
    phrase_html = f'<code class="pa-phrase">{esc(phrase)}</code>' if phrase else ""
    return (
        '<section class="primary-action card simple-pa">'
        '<div class="pa-tag">🎯 Do this next</div>'
        f'<h2 class="pa-title">{esc(a.get("title"))}</h2>'
        f'{phrase_html}'
        f'<p class="pa-why">{esc(a.get("why", ""))}</p>'
        '</section>'
    )


def simple_recs_block(recs: list) -> str:
    """Recommendations, trimmed: dot · type · name · one line · install only."""
    if not recs:
        return (
            '<section><h2 class="sec">✨ Recommended for you</h2>'
            '<div class="card"><p class="simple-note">No catalog-backed recommendation '
            'for this run yet.</p></div></section>'
        )
    cards = []
    for r in recs:
        conf = r.get("confidence", "med")
        dots = CONF_DOTS.get(conf, CONF_DOTS["med"])
        color = CONF.get(conf, CONF["med"])
        name = esc(r.get("name", "?"))
        url = r.get("source_url")
        name_html = f'<a href="{esc(url)}">{name}</a>' if url else name
        line = esc((r.get("description") or r.get("job") or "")[:110])
        install = (r.get("install") or [""])[0]
        install_html = f'<code class="srinstall">{esc(install)}</code>' if install else ""
        cards.append(
            '<div class="card simple-rec">'
            f'<div class="srhead"><span class="dots" style="color:{color}">{dots}</span>'
            f'<span class="rtype">{esc(r.get("type", "skill"))}</span>'
            f'<span class="rname">{name_html}</span></div>'
            f'<p class="srline">{line}</p>'
            f'{install_html}'
            '</div>'
        )
    note = ""
    if len(recs) < 3:
        plural = "" if len(recs) == 1 else "es"
        note = (
            f'<p class="simple-note">Only {len(recs)} catalog-backed match{plural} '
            'genuinely fit your usage — nothing was invented to pad the list.</p>'
        )
    return '<section><h2 class="sec">✨ Recommended for you</h2>' + note + "".join(cards) + '</section>'


def simple_coach_block(coaching: list) -> str:
    """Coaching: the problem (what you do), why it's worth changing, the action,
    and the evidence one-liner. Each line is optional so a sparse card degrades
    gracefully."""
    if not coaching:
        return ""
    cards = []
    for c in coaching:
        saw = esc(c.get("saw", ""))
        why = esc(c.get("costs", ""))
        what_html = f'<p class="scwhat"><b>What you do.</b> {saw}</p>' if saw else ""
        why_html = f'<p class="scwhy"><b>Why change.</b> {why}</p>' if why else ""
        cards.append(
            '<div class="card simple-coach">'
            f'<h4>{esc(c.get("title", ""))}</h4>'
            f'{what_html}'
            f'{why_html}'
            f'<p class="scbetter"><b>Try this.</b> {esc(c.get("better", ""))}</p>'
            f'<p class="scsaw">{esc(c.get("evidence", ""))}</p>'
            '</div>'
        )
    return '<section><h2 class="sec">⚑ Habits to tweak</h2>' + "".join(cards) + '</section>'


def simple_token_block(token_tips: list) -> str:
    """Token-cost tips — each cites a hard count and names a cheaper habit."""
    if not token_tips:
        return ""
    cards = []
    for t in token_tips:
        cards.append(
            '<div class="card simple-coach simple-token">'
            f'<h4>{esc(t.get("title", ""))}</h4>'
            f'<p class="scbetter"><b>Try this.</b> {esc(t.get("tip", ""))}</p>'
            f'<p class="scsaw">{esc(t.get("evidence", ""))}</p>'
            '</div>'
        )
    return ('<section><h2 class="sec">💸 Trim token usage</h2>'
            '<p class="simple-note">Cheaper habits that keep context small — '
            'each tied to a real count.</p>' + "".join(cards) + '</section>')


def simple_view(payload: dict, m: dict, grove_html: str,
                share_cards: dict | None = None, share_caption: str = "") -> str:
    """The default, easy-to-scan view: Primary action · Recommendations ·
    Coaching · Daimon Grove, with a button to switch to the advanced report and
    a one-tap button to share a brandable archetype card."""
    days = esc(m.get("days", 28))
    # Full five-part archetype section (label, title, tagline, why/strength/
    # watch-out/next ritual) — same card the advanced view uses.
    arch_html = archetype_card(payload.get("archetype") or {})
    pa = simple_primary_action_card(payload.get("primary_action") or {})
    recs = simple_recs_block(payload.get("recommendations") or [])
    coach = simple_coach_block(payload.get("coaching") or [])
    token = simple_token_block(payload.get("token_tips") or [])

    # Share card: opt-in, leak-free. Each size's SVG lives in a non-rendered
    # <template>; JS rasterizes the picked size to PNG and opens the native
    # share sheet (caption pre-filled), falling back to download + X intent.
    share_btn = ""
    share_block = ""
    share_cards = share_cards or {}
    if share_cards:
        caption_js = json.dumps({"caption": share_caption,
                                 "hashtag": "SkillsDaimon"})
        templates = "".join(
            f'<template id="sd-card-{sz}">{svg}</template>'
            for sz, svg in share_cards.items() if svg
        )
        preview_svg = share_cards.get("square") or next(iter(share_cards.values()), "")
        share_btn = ('<button class="viewtoggle share" onclick="sdShareCard()">'
                     '🔗 Share my archetype</button>')
        share_block = (
            f'<script>window.__sdShare={caption_js};</script>'
            f'{templates}'
            '<details class="share-preview"><summary>Preview &amp; pick a size</summary>'
            '<div class="share-sizes"><label for="sd-size">Size</label>'
            '<select id="sd-size" onchange="sdSwapPreview()">'
            '<option value="square">Square · 1080×1080 (feed)</option>'
            '<option value="story">Story · 1080×1920 (IG/TikTok)</option>'
            '<option value="link">Link · 1200×630 (X/LinkedIn)</option>'
            '</select>'
            '<button class="viewtoggle" onclick="sdDownloadCard()">Download PNG</button>'
            '</div>'
            f'<div id="sd-preview">{preview_svg}</div>'
            '<p class="share-hint">Downloads as a PNG — on mobile it also opens '
            'your share sheet with a caption ready. Aggregate only; no project '
            'names or counts. Nothing is uploaded.</p>'
            '</details>'
        )

    return (
        '<div id="view-simple" class="simpleview">'
        '<div class="simple-top">'
        f'<div class="simple-title">🏛 Skills Daimon <span>· last {days} days</span></div>'
        '<div class="simple-actions">'
        + share_btn
        + '<button class="viewtoggle" id="view-toggle" onclick="sdToggleView()">Advanced view →</button>'
        + '</div>'
        '</div>'
        + arch_html + share_block + pa + recs + coach + token + (grove_html or "")
        + '<div class="simple-foot">🔒 Local only · evidence from your own sessions · nothing left this machine</div>'
        '</div>'
    )


def render(payload: dict) -> str:
    m = payload.get("meta", {})
    recs = payload.get("recommendations", [])
    gaps = payload.get("gaps", [])
    coaching = payload.get("coaching", [])
    charts = payload.get("charts", {})
    work_recap = payload.get("work_recap", {})

    level_chip_html, quest_html, grove_html, game_state = _gamify_blocks(payload, m)

    # Build the shareable archetype cards (3 sizes) from non-identifying bits
    # only, plus a pre-filled caption with a curiosity hook.
    share_cards: dict = {}
    share_caption = ""
    if game_state:
        try:
            archetype = payload.get("archetype") or {}
            work_mix = (payload.get("work_recap") or {}).get("mix") or {}
            grove = game_state.get("grove") or {}
            level = int(game_state.get("daimon_level") or 0)
            badge_count = int(game_state.get("badge_count") or 0)
            balanced = bool(grove.get("balanced"))
            sessions = int(m.get("sessions") or 0)
            days = int(m.get("days") or 0)
            for sz in ("square", "story", "link"):
                share_cards[sz] = build_share_card_svg(
                    archetype, work_mix, sessions, days,
                    level, badge_count, balanced, size=sz,
                )
            arch_title = (archetype.get("title") or "").strip() or "an evolving archetype"
            share_caption = (
                f"I'm {arch_title} this month — {sessions:,} Claude Code sessions in. "
                "What's your archetype?"
            )
        except Exception:
            share_cards, share_caption = {}, ""

    # ─── COMBINED HERO (verdict + archetype + Daimon Level in one card) ───
    hero_html = hero_card(
        payload.get("verdict") or {},
        payload.get("archetype") or {},
        m,
        level_chip_html or "",
    )
    if not hero_html:
        date = esc(m.get("date", ""))
        days = esc(m.get("days", 14))
        hero_html = (
            '<section class="hero-card">'
            '<div class="hv-sub">Skills Daimon · Last '
            f'{days} days · generated {date}</div>'
            '<h1 class="hero-arch-title">Snapshot</h1>'
            '<p class="hero-summary">No verdict supplied; showing the underlying signals below.</p>'
            '</section>'
        )
    arch_html = ""  # merged into hero_html; kept for assembly compatibility

    # ─── PRIMARY ACTION CARD ───────────────────────────────────────────────
    pa_html = primary_action_card(payload.get("primary_action") or {})

    # ─── CATALOG BADGES (small, under the hero) ────────────────────────────
    badges = "".join(
        f'<span class="badge badge-dark">{esc(c)}</span>' for c in m.get("catalogs", [])
    )
    catalog_strip = (
        '<section class="srcstrip card">'
        '<div class="srclabel-dark">Skill sources searched <span>— marketplaces, registries &amp; MCP catalogs</span></div>'
        f'<div class="badges">{badges}</div>'
        '</section>'
    ) if badges else ""

    # ─── RECAP ─────────────────────────────────────────────────────────────
    recap_html = (
        '<h2 class="sec">🧭 What you\'ve been working on</h2>' + recap_strip(work_recap)
        if work_recap.get("top_projects") else ""
    )

    # ─── WORKFLOW SIGNALS (was Health check) ───────────────────────────────
    scorecard = payload.get("scorecard", [])
    history_by_key: dict[str, list] = {}
    try:
        import history as _history  # type: ignore
        entries = _history.read_last(8, window_days=int(m.get("days") or 0) or None)
        for e in entries:
            sc = e.get("scorecard") or {}
            for k, v in sc.items():
                if isinstance(v, (int, float)):
                    history_by_key.setdefault(k, []).append(v)
    except Exception:
        history_by_key = {}

    scorecard_html = (
        '<h2 class="sec">🩺 Workflow signals</h2>'
        '<p class="seclead">How you\'re working, scored where there\'s a clear better way.</p>'
        + scorecard_strip(scorecard, history_by_key)
        if scorecard else ""
    )

    # ─── RECS + GAPS + COACHING (unchanged ordering) ───────────────────────
    recs_html = (
        '<h2 class="sec">✨ Recommendations</h2>' + "".join(rec_card(r) for r in recs)
        if recs else ""
    )
    gaps_html = (
        '<div class="softbreak"></div>'
        '<h2 class="sec">🛠️ Worth building yourself</h2>'
        '<p class="seclead">You do these a lot, but no existing skill matches — so you\'d make your own.</p>'
        '<ul class="gaps">'
        + "".join(gap_item(g) for g in gaps) + "</ul>"
        if gaps else ""
    )
    coach_html = (
        '<div class="coachbreak"></div>'
        '<h2 class="sec big">⚑ Coaching — small habits worth changing</h2>'
        + "".join(coach_card(c) for c in coaching)
        if coaching else ""
    )

    # Anthropic /insights cross-reference: pull the two qualitative sections
    # (New Ways / On the Horizon) from the locally cached report, plus a
    # sidebar link to the full report. All optional — skipped if missing.
    insights_report = Path.home() / ".claude" / "usage-data" / "report.html"
    insights_sections = _extract_insights_sections(insights_report) if insights_report.is_file() else None
    patterns_html = ""
    horizon_html  = ""
    if insights_sections:
        attrib = (
            '<div class="insights-attrib">From your latest Anthropic /insights run</div>'
        )
        if insights_sections.get("patterns"):
            patterns_html = attrib + insights_sections["patterns"]
        if insights_sections.get("horizon"):
            horizon_html  = attrib + insights_sections["horizon"]

    # Sidebar — only entries whose section actually rendered show up.
    nav_items = [
        ("hero",     "🏛", "Verdict",       hero_html or arch_html),
        ("action",   "🎯", "Next action",   pa_html),
        ("mission",  "⚔",  "Mission",       quest_html),
        ("recs",     "✨", "Recommendations", recs_html),
        ("signals",  "🩺", "Signals",       scorecard_html),
        ("coaching", "⚑",  "Coaching",      coach_html),
        ("gaps",     "🛠",  "Build",         gaps_html),
        ("grove",    "🌲", "Grove",         grove_html),
        ("recap",    "🧭", "What you did",  recap_html),
        ("patterns", "💡", "New ways",      patterns_html),
        ("horizon",  "🔮", "On the horizon", horizon_html),
    ]

    external_links = []
    if insights_report.is_file():
        external_links.append((
            insights_report.as_uri(),
            "📊",
            "Full /insights",
        ))

    sidebar_html = (
        '<aside class="sidebar" aria-label="Jump to section">'
        + "".join(
            f'<a class="snav" href="#sec-{slug}" title="{label}">'
            f'<span class="snav-ico" aria-hidden="true">{ico}</span>'
            f'<span class="snav-lbl">{label}</span></a>'
            for slug, ico, label, present in nav_items if present
        )
        + ("".join(
            f'<a class="snav snav-ext" href="{esc(url)}" target="_blank" rel="noopener" '
            f'title="{label} (opens in new tab)">'
            f'<span class="snav-ico" aria-hidden="true">{ico}</span>'
            f'<span class="snav-lbl">{label} <span class="snav-out">↗</span></span></a>'
            for url, ico, label in external_links
        ) if external_links else "")
        + '</aside>'
    )

    def _sec(slug: str, html_block: str) -> str:
        return f'<section id="sec-{slug}">{html_block}</section>' if html_block else ""

    # Simple view (default) — easy to scan; Advanced view (hidden) is the full
    # report. A button on each toggles between them. One self-contained file.
    simple_html = simple_view(payload, m, grove_html, share_cards, share_caption)
    advanced_html = (
        '<div id="view-advanced" style="display:none">'
        + sidebar_html
        + '<div class="wrap">'
        + '<div class="viewbar"><button class="viewtoggle" onclick="sdToggleView()">← Simple view</button></div>'
        + _sec("hero",     (hero_html or "") + (arch_html or ""))
        + _sec("action",   pa_html)
        + _sec("mission",  quest_html)
        + _sec("recs",     recs_html + catalog_strip)
        + _sec("signals",  scorecard_html)
        + _sec("gaps",     gaps_html)
        + _sec("coaching", coach_html)
        + _sec("grove",    grove_html)
        + _sec("recap",    recap_html)
        + _sec("patterns", patterns_html)
        + _sec("horizon",  horizon_html)
        + '<footer>🏛 Generated by <b>Skills Daimon</b> · evidence from your own '
          'Claude Code sessions · nothing left this machine 🔒</footer>'
        + "</div></div>"
    )

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Skills Daimon</title><style>" + CSS + SIMPLE_CSS + SHARE_CSS + "</style></head><body>"
        + simple_html
        + advanced_html
        + ACTIVE_SECTION_JS + VIEW_TOGGLE_JS + SHARE_JS + "</body></html>"
    )


def render_redacted(payload: dict) -> str:
    """Render with the shared redactor on input payload and HTML write output."""
    safe_payload = redact_in(payload)
    return redact_in(render(safe_payload))


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", "--stdin"):
        payload = json.loads(Path(sys.argv[1]).read_text())
    else:
        payload = json.loads(sys.stdin.read())

    # Belt-and-braces: scrub plausible secrets in every string before render.
    payload = redact_in(payload)

    date = payload.get("meta", {}).get("date") or _dt.date.today().isoformat()
    out_dir = Path.home() / ".claude" / "skills" / "skills-daimon" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"skills-daimon-{date}.html"
    out.write_text(redact_in(render(payload)), encoding="utf-8")

    print(json.dumps({
        "path": str(out),
        "url": out.as_uri(),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
