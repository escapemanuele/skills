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
  "coaching": [{"title": "...", "evidence": "...", "costs": "...", "better": "..."}],
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


def sparkline(values, w=72, h=18) -> str:
    """Inline-SVG sparkline. Requires ≥3 distinct points or returns empty."""
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
    return (
        f'<svg class="sline" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img"><polyline fill="none" stroke="currentColor" '
        f'stroke-width="1.5" points="{poly}"/></svg>'
    )


def _delta_html(values: list, current) -> str:
    """\"up from 10\" delta vs the most recent distinct prior value."""
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
        note = esc(it.get("note", ""))
        explain = esc(it.get("explain", ""))
        body = note
        if explain:
            body += f'<div class="scexplain">{explain}</div>'
        # Trend bits (only when history has the key)
        key = it.get("history_key")
        series = history_by_key.get(key) if key else None
        spark = sparkline(series) if series else ""
        cur = it.get("current_number")
        delta = _delta_html(series, cur) if series and cur is not None else ""
        rows.append(
            '<details class="scrow">'
            '<summary>'
            '<span class="scchev">▸</span>'
            f'<div class="scmain"><div class="sclabel">{esc(it.get("label", ""))}</div></div>'
            f'<div class="scval">{esc(it.get("value", ""))}{delta}</div>'
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
        f'<p class="desc">{esc(r.get("description", ""))}</p>'
        f'<div class="install">{install}</div>'
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
        f'<p><span class="tag ev-t">What we saw</span> {esc(c.get("evidence", ""))}</p>'
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


def hero_verdict_card(verdict: dict, meta: dict) -> str:
    """The hero: named verdict, summary, evidence chips, next move."""
    if not verdict or not verdict.get("name"):
        return ""
    chips = "".join(
        f'<span class="chip">{esc(c)}</span>' for c in (verdict.get("evidence_chips") or [])
    )
    next_phrase = (verdict.get("next_phrase") or "").strip()
    next_html = (
        f'<div class="hv-next"><span class="hv-next-label">Next move</span>'
        f'<code class="hv-phrase">{esc(next_phrase)}</code></div>'
        if next_phrase else ""
    )
    days = esc(meta.get("days", "14"))
    date = esc(meta.get("date", ""))
    return (
        '<section class="hero-verdict">'
        f'<div class="hv-sub">Skills Daimon · Last {days} days · generated {date}</div>'
        f'<h1 class="hv-name">{esc(verdict.get("name"))}</h1>'
        f'<p class="hv-summary">{esc(verdict.get("summary", ""))}</p>'
        f'<div class="hv-chips">{chips}</div>'
        f'{next_html}'
        '</section>'
    )


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
.ev{margin:6px 0}.desc{color:#374151;margin:6px 0}
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
details.scrow summary{display:flex;align-items:center;gap:12px;padding:14px 0;cursor:pointer;
  list-style:none;outline:none}
details.scrow summary::-webkit-details-marker{display:none}
.scchev{color:%(MUTED)s;font-size:12px;transition:transform .15s;flex-shrink:0}
details.scrow[open] .scchev{transform:rotate(90deg)}
.scmain{flex:1;min-width:0}
.sclabel{font-weight:600;font-size:15px}
.scval{font-variant-numeric:tabular-nums;font-weight:700;font-size:15px;white-space:nowrap;color:%(MUTED)s}
.scspark{color:%(ACCENT)s;opacity:.85;flex-shrink:0;min-width:72px;text-align:right}
.delta{font-size:12px;font-weight:600;margin-left:6px}
.delta.up{color:%(BAD)s}
.delta.down{color:%(GOOD)s}
.scrow .verdict{margin:0;flex-shrink:0}
.scbody{padding:0 0 16px 24px;color:#374151;font-size:13.5px;line-height:1.55}
.scexplain{margin-top:8px;padding:10px 12px;background:#f7f8fb;border-radius:8px;color:%(INK)s}
.verdict{text-align:center;font-size:12.5px;font-weight:600;margin:12px auto 0;
  padding:5px 12px;border-radius:999px;display:block;width:fit-content}
.verdict.good{background:#ECFDF5;color:%(GOOD)s}
.verdict.watch{background:#FFFBEB;color:%(WATCH)s}
.verdict.bad{background:#FEF2F2;color:%(BAD)s}
.verdict.nodata{background:#F3F4F6;color:%(MUTED)s}
/* hero verdict */
.hero-verdict{background:linear-gradient(135deg,%(ACCENT)s,#7c3aed);color:#fff;
  border-radius:20px;padding:30px 32px;margin-bottom:20px;box-shadow:0 8px 28px rgba(109,40,217,.18)}
.hv-sub{font-size:12px;text-transform:uppercase;letter-spacing:.8px;opacity:.85;margin-bottom:10px}
.hv-name{margin:0 0 10px;font-size:34px;font-weight:800;letter-spacing:-.5px;line-height:1.1}
.hv-summary{margin:0 0 14px;font-size:16px;line-height:1.45;opacity:.95;max-width:60ch}
.hv-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.chip{background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.25);
  padding:4px 10px;border-radius:999px;font-size:12px;font-variant-numeric:tabular-nums}
.hv-next{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  padding:12px 14px;background:rgba(255,255,255,.13);border-radius:12px}
.hv-next-label{font-size:11px;text-transform:uppercase;letter-spacing:.8px;opacity:.85}
.hv-phrase{display:inline-block;background:#0f172a;color:#fff;padding:7px 11px;
  border-radius:8px;font:13.5px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
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
/* daimon level chip (inside archetype card) */
.daimon-chip{display:inline-flex;align-items:center;gap:6px;float:right;
  background:%(ACCENT_SOFT)s;color:%(ACCENT)s;border:1px solid %(GAP)s;
  border-radius:999px;padding:4px 10px;font-size:12px;font-weight:600}
.daimon-chip .dc-val{font-size:14px;font-weight:800;margin:0 2px}
.daimon-chip .dc-xp{opacity:.75;font-weight:500}
/* quest card */
.quest{border-left:5px solid %(INFO)s;background:#F8FAFF}
.quest-tag{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:%(INFO)s;
  font-weight:700;margin-bottom:6px}
.quest-title{margin:0 0 8px;font-size:18px;font-weight:700;color:%(INK)s}
.quest-why{margin:6px 0;color:#374151}
.quest-do{margin:8px 0}
.quest-do-label{font-size:11px;font-weight:700;color:%(INFO)s;text-transform:uppercase;letter-spacing:.5px}
.quest-do code{display:inline-block;margin-left:6px}
.quest-reward{margin:10px 0 0;color:#374151;font-size:13.5px}
.quest-note{color:%(MUTED)s;font-size:12px}
/* grove */
.grove-card{padding:14px 18px}
.grove-card svg{display:block;margin:0 auto 8px}
.grove-tracks{width:100%%;border-collapse:collapse;font-size:13.5px;margin:8px 0 6px}
.grove-tracks th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.5px;
  color:%(MUTED)s;border-bottom:1px solid %(GAP)s;padding:6px 8px}
.grove-tracks td{padding:6px 8px;border-bottom:1px solid #f1f2f5}
.grove-tracks td.num,.grove-tracks th.num{text-align:right;font-variant-numeric:tabular-nums}
.grove-ev{color:%(MUTED)s;font-size:13px}
.delta-flat{color:%(MUTED)s}
.grove-badges{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px}
.grove-badge{font-size:12px;padding:3px 10px;border-radius:999px;border:1px solid %(GAP)s}
.grove-badge.on{background:#ECFDF5;border-color:#A7F3D0;color:%(GOOD)s;font-weight:600}
.grove-badge.off{color:%(MUTED)s;background:#fff}
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
""" % {
    "INK": INK, "ACCENT": ACCENT, "ACCENT_SOFT": ACCENT_SOFT,
    "MUTED": MUTED, "GAP": GAP, "BG": BG,
    "GOOD": GOOD, "WATCH": WATCH, "BAD": BAD, "INFO": INFO,
}


def _gamify_blocks(payload: dict, m: dict):
    """Return (level_chip_html, quest_card_html, grove_section_html) — empty
    strings if gamification is unavailable. Pulls fresh history so deltas
    are honest. Never raises into the renderer."""
    try:
        import gamify as _gamify    # type: ignore
        import history as _history  # type: ignore
    except Exception:
        return "", "", ""
    try:
        entries = _history.read_last(8, window_days=int(m.get("days") or 0) or None)
    except Exception:
        entries = []
    try:
        gs = _gamify.build_game_state(payload, entries, today=m.get("date"),
                                       window_days=m.get("days"))
    except Exception:
        return "", "", ""

    # Top: a compact Daimon Level chip next to the archetype.
    level_chip = (
        f'<div class="daimon-chip" title="Daimon Level">'
        f'<span class="dc-label">Daimon Level</span>'
        f'<span class="dc-val">{int(gs["daimon_level"])}</span>'
        f'<span class="dc-xp">· {int(gs["xp_total"])} XP</span>'
        f'</div>'
    )

    # Quest card (after primary action).
    quest = gs.get("active_quest")
    if quest:
        quest_html = (
            '<section class="quest card">'
            '<div class="quest-tag">⚜ Quest offered</div>'
            f'<h2 class="quest-title">{esc(quest.get("title"))}</h2>'
            f'<p class="quest-why">{esc(quest.get("why",""))}</p>'
            f'<div class="quest-do"><span class="quest-do-label">Do</span>'
            f' <code>{esc(quest.get("do",""))}</code></div>'
            f'<p class="quest-reward"><b>Reward.</b> {esc(quest.get("reward",""))} '
            '<span class="quest-note">(XP is awarded only when the next scan verifies the improvement.)</span></p>'
            '</section>'
        )
    else:
        quest_html = ""

    # Grove + XP movements (near trends, below activity bars).
    earned = sum(1 for b in gs.get("badges", []) if b.get("earned"))
    track_rows = []
    for tname, t in gs["tracks"].items():
        delta = int(t["delta"])
        delta_html = (
            f'<span class="delta down">+{delta}</span>' if delta > 0
            else '<span class="delta-flat">·</span>'
        )
        track_rows.append(
            f'<tr><td>{esc(tname)}</td>'
            f'<td class="num">L{int(t["level"])}</td>'
            f'<td class="num">{int(t["xp"])} XP</td>'
            f'<td class="num">{delta_html}</td>'
            f'<td class="grove-ev">{esc(t.get("evidence",""))}</td></tr>'
        )
    badges_html = " · ".join(
        f'<span class="grove-badge {"on" if b["earned"] else "off"}">{esc(b["name"])}</span>'
        for b in gs.get("badges", [])
    )
    grove_html = (
        '<h2 class="sec">🌲 Daimon Grove</h2>'
        '<p class="seclead">Conservative gamification. XP is only awarded when the next scan verifies the improvement — never just for running the report.</p>'
        '<div class="card grove-card">'
        f'{_gamify.render_grove_svg(gs["grove"])}'
        '<table class="grove-tracks">'
        '<thead><tr><th>Track</th><th class="num">Level</th><th class="num">XP</th>'
        '<th class="num">Δ this run</th><th>Evidence</th></tr></thead>'
        f'<tbody>{"".join(track_rows)}</tbody></table>'
        f'<div class="grove-badges">{badges_html}</div>'
        f'<p class="cap">Badges earned: {earned} · Quests offered: {int(gs.get("quests_offered_count",0))} · '
        f'Quests verified: {int(gs.get("quests_completed_count",0))}</p>'
        '</div>'
    )

    return level_chip, quest_html, grove_html


def render(payload: dict) -> str:
    m = payload.get("meta", {})
    recs = payload.get("recommendations", [])
    gaps = payload.get("gaps", [])
    coaching = payload.get("coaching", [])
    charts = payload.get("charts", {})
    work_recap = payload.get("work_recap", {})

    level_chip_html, quest_html, grove_html = _gamify_blocks(payload, m)

    # ─── HERO VERDICT (replaces the old hero/title) ────────────────────────
    hero_html = hero_verdict_card(payload.get("verdict") or {}, m)
    # If no verdict was provided (payload missing it) fall back to a tiny
    # title bar so the report still has a top.
    if not hero_html:
        date = esc(m.get("date", ""))
        days = esc(m.get("days", 14))
        hero_html = (
            '<section class="hero-verdict">'
            '<div class="hv-sub">Skills Daimon · Last '
            f'{days} days · generated {date}</div>'
            '<h1 class="hv-name">Snapshot</h1>'
            '<p class="hv-summary">No verdict supplied; showing the underlying signals below.</p>'
            '</section>'
        )

    # ─── ARCHETYPE CARD (5-part) + Daimon Level chip ───────────────────────
    arch_inner = archetype_card(payload.get("archetype") or {})
    if arch_inner and level_chip_html:
        # Inject the level chip right after the opening section tag.
        arch_html = arch_inner.replace(
            '<section class="archetype-card card">',
            '<section class="archetype-card card">' + level_chip_html,
            1,
        )
    else:
        arch_html = arch_inner

    # ─── PRIMARY ACTION CARD ───────────────────────────────────────────────
    pa_html = primary_action_card(payload.get("primary_action") or {})

    # ─── CATALOG BADGES (small, under the hero) ────────────────────────────
    badges = "".join(
        f'<span class="badge badge-dark">{esc(c)}</span>' for c in m.get("catalogs", [])
    )
    catalog_strip = (
        '<section class="srcstrip card">'
        '<div class="srclabel-dark">Skill sources searched <span>— marketplaces &amp; registries</span></div>'
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
        '<p class="seclead">How you\'re working, scored where there\'s a clear better way. Good · Watch · Needs action · No data.</p>'
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

    # ─── ACTIVITY BARS (context only, at the bottom) ───────────────────────
    chart_blocks = []
    if charts.get("tool_use_top"):
        chart_blocks.append(bar_chart(charts["tool_use_top"], "Tool use", top=8, color=BAR))
    if charts.get("bash_verbs_top"):
        chart_blocks.append(bar_chart(charts["bash_verbs_top"], "Top bash verbs", top=10, color=INFO))
    charts_html = (
        '<h2 class="sec">📊 Your activity — just for context</h2>'
        '<p class="seclead">Raw counts, no score. Just what you ran most.</p>'
        f'<div class="charts">{"".join(chart_blocks)}</div>'
        if chart_blocks else ""
    )

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Skills Daimon</title><style>" + CSS + "</style></head><body>"
        '<div class="wrap">'
        + hero_html
        + arch_html
        + pa_html
        + quest_html        # the one active quest, right after the primary action
        + catalog_strip
        + recs_html         # main focus, immediately after the primary action
        + recap_html
        + scorecard_html
        + gaps_html
        + coach_html
        + charts_html
        + grove_html        # Daimon Grove: gamification stays below the evidence
        + '<footer>🏛 Generated by <b>Skills Daimon</b> · evidence from your own '
          'Claude Code sessions · nothing left this machine 🔒</footer>'
        "</div></body></html>"
    )


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
    out.write_text(render(payload), encoding="utf-8")

    print(json.dumps({
        "path": str(out),
        "url": out.as_uri(),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
