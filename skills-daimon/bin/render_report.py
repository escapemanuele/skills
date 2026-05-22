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


# --- palette ----------------------------------------------------------------
ACCENT = "#4f46e5"
ACCENT_SOFT = "#eef2ff"
INK = "#1e2330"
MUTED = "#6b7280"
BAR = "#6366f1"
BAR_WARN = "#f59e0b"
GAP = "#e5e7eb"
CONF = {"high": "#16a34a", "med": "#f59e0b", "low": "#9ca3af"}
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


def donut(bypass: int, native: int) -> str:
    """Donut showing bypass vs native tool usage."""
    total = bypass + native
    if total <= 0:
        return ""
    frac = bypass / total
    import math

    r, cx, cy, sw = 70, 90, 90, 26
    circ = 2 * math.pi * r
    dash = circ * frac
    pct = round(frac * 100)
    return (
        '<div class="chart"><h3>Native tools vs raw shell</h3>'
        f'<svg viewBox="0 0 180 180" width="200" role="img">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{GAP}" '
        f'stroke-width="{sw}"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{BAR_WARN}" '
        f'stroke-width="{sw}" stroke-dasharray="{dash:.1f} {circ:.1f}" '
        f'stroke-dashoffset="0" transform="rotate(-90 {cx} {cy})" '
        f'stroke-linecap="round"/>'
        f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" class="dn">{pct}%</text>'
        f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" class="dl">raw shell</text>'
        '</svg>'
        f'<p class="cap"><b style="color:{BAR_WARN}">{bypass}</b> raw '
        f'grep/find/cat &nbsp;·&nbsp; <b>{native}</b> native Grep/Glob/Read</p>'
        '</div>'
    )


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
    return (
        '<div class="card coach">'
        f'<h4>{esc(c.get("title", ""))}</h4>'
        f'<p><span class="tag ev-t">What we saw</span> {esc(c.get("evidence", ""))}</p>'
        f'<p><span class="tag cost-t">Why it matters</span> {esc(c.get("costs", ""))}</p>'
        f'<p><span class="tag best-t">Try this</span> {esc(c.get("better", ""))}</p>'
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


def usage_card(u: dict) -> str:
    """Token totals + approximate cost + per-model bar."""
    if not u:
        return ""
    t = u.get("totals", {})
    in_tok = (t.get("input", 0) or 0) + (t.get("cache_read", 0) or 0) + (t.get("cache_write", 0) or 0)
    out_tok = t.get("output", 0) or 0
    cost = u.get("est_cost_usd_total")
    by_model = {m: (v.get("est_cost_usd") or 0) for m, v in (u.get("by_model") or {}).items()}
    bars = bar_chart(by_model, "Est. cost by model ($)", top=6, color="#7c3aed") if any(by_model.values()) else ""
    cost_str = f"~${cost:,.0f}" if cost else "—"
    return (
        '<div class="card usage">'
        '<div class="bignums">'
        f'<div class="bn"><b>{esc(fmt_tokens(in_tok))}</b><span>tokens in (incl. cache)</span></div>'
        f'<div class="bn"><b>{esc(fmt_tokens(out_tok))}</b><span>tokens out</span></div>'
        f'<div class="bn"><b>{esc(cost_str)}</b><span>est. cost</span></div>'
        '</div>'
        f'<p class="cap">Approximate, priced {esc(u.get("pricing_as_of", "?"))}. '
        'Cache reads are large and cheap — normal.</p>'
        f'{bars}'
        '</div>'
    )


KIND_ICON = {"dev": "⚙", "writing": "✍", "data": "▦", "ops": "◷", "other": "•"}


def recap_strip(w: dict) -> str:
    """Top projects (kind-tagged) + a work-mix bar."""
    if not w:
        return ""
    import os
    rows = []
    for p in (w.get("top_projects") or [])[:5]:
        path = p.get("path", "")
        name = os.path.basename(path.rstrip("/")) or path
        kind = p.get("kind", "other")
        icon = KIND_ICON.get(kind, "•")
        rows.append(
            f'<tr><td>{icon} {esc(name)}</td><td class="num">{esc(p.get("sessions", 0))}</td>'
            f'<td class="num">{esc(fmt_tokens(p.get("tokens", 0)))}</td>'
            f'<td><span class="kind k-{esc(kind)}">{esc(kind)}</span></td></tr>'
        )
    mix = w.get("mix") or {}
    mix_bar = "".join(
        f'<span class="seg k-{esc(k)}" style="width:{v}%" title="{esc(k)} {v}%"></span>'
        for k, v in mix.items() if v
    )
    mix_legend = " · ".join(f'{esc(k)} {v}%' for k, v in mix.items() if v)
    return (
        '<div class="card recap">'
        f'<div class="mixbar">{mix_bar}</div><p class="cap">{esc(mix_legend)}</p>'
        '<table class="rt"><thead><tr><th>Project</th><th class="num">Sessions</th>'
        '<th class="num">Tokens</th><th>Focus</th></tr></thead><tbody>'
        f'{"".join(rows)}</tbody></table>'
        '</div>'
    )


def gap_item(g: dict) -> str:
    init = g.get("init")
    init_html = f' <code>{esc(init)}</code>' if init else ""
    return (
        f'<li><b>{esc(g.get("tag", ""))}</b> — {esc(g.get("note", ""))}{init_html}</li>'
    )


CSS = """
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  color:%(INK)s;background:#f7f8fb;-webkit-font-smoothing:antialiased}
.wrap{max-width:920px;margin:0 auto;padding:32px 24px 64px}
header.hero{background:linear-gradient(135deg,%(ACCENT)s,#7c3aed);color:#fff;
  border-radius:18px;padding:28px 30px;margin-bottom:28px}
header.hero h1{margin:0 0 4px;font-size:26px;letter-spacing:-.3px}
header.hero .sub{opacity:.9;font-size:14px}
.stats{display:flex;gap:28px;margin-top:18px;flex-wrap:wrap}
.stats .s b{font-size:24px;display:block;line-height:1}
.stats .s span{font-size:12px;opacity:.85;text-transform:uppercase;letter-spacing:.5px}
.badges{margin-top:16px;display:flex;gap:8px;flex-wrap:wrap}
.badge{background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.25);
  padding:3px 10px;border-radius:999px;font-size:12px}
h2.sec{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:%(MUTED)s;
  margin:34px 0 14px;border-bottom:1px solid %(GAP)s;padding-bottom:8px}
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
.coach h4{margin:0 0 8px;font-size:16px}
.coach p{margin:5px 0}
.tag{display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.5px;padding:1px 7px;border-radius:5px;margin-right:6px}
.ev-t{background:#eef2ff;color:#4f46e5}.cost-t{background:#fef2f2;color:#dc2626}
.best-t{background:#f0fdf4;color:#16a34a}
ul.gaps{list-style:none;padding:0;margin:0}
ul.gaps li{background:#fff;border:1px solid %(GAP)s;border-left:3px solid %(MUTED)s;
  border-radius:10px;padding:12px 16px;margin-bottom:10px}
ul.gaps code{display:inline-block;margin-top:6px}
.charts{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start}
.chart{background:#fff;border:1px solid %(GAP)s;border-radius:14px;padding:16px 18px;flex:1;min-width:280px}
.chart h3{margin:0 0 12px;font-size:14px}
.bl{font-size:12px;fill:%(INK)s}.bv{font-size:12px;fill:%(MUTED)s}
.dn{font-size:30px;font-weight:700;fill:%(INK)s}.dl{font-size:11px;fill:%(MUTED)s}
.cap{font-size:12px;color:%(MUTED)s;margin:10px 0 0;text-align:center}
.bignums{display:flex;gap:36px;flex-wrap:wrap}
.bignums .bn b{font-size:30px;display:block;line-height:1.1;color:%(INK)s}
.bignums .bn span{font-size:12px;color:%(MUTED)s}
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
    "MUTED": MUTED, "GAP": GAP,
}


def render(payload: dict) -> str:
    m = payload.get("meta", {})
    recs = payload.get("recommendations", [])
    gaps = payload.get("gaps", [])
    coaching = payload.get("coaching", [])
    charts = payload.get("charts", {})
    usage = payload.get("usage", {})
    work_recap = payload.get("work_recap", {})

    badges = "".join(
        f'<span class="badge">{esc(c)}</span>' for c in m.get("catalogs", [])
    )
    hero = (
        '<header class="hero"><h1>Your skill fit</h1>'
        f'<div class="sub">Last {esc(m.get("days", 14))} days · '
        f'generated {esc(m.get("date", ""))}</div>'
        '<div class="stats">'
        f'<div class="s"><b>{esc(m.get("sessions", "?"))}</b><span>sessions</span></div>'
        f'<div class="s"><b>{esc(m.get("projects", "?"))}</b><span>projects</span></div>'
        f'<div class="s"><b>{len(recs)}</b><span>recommendations</span></div>'
        f'<div class="s"><b>{len(coaching)}</b><span>coaching tips</span></div>'
        '</div>'
        f'<div class="badges">{badges}</div></header>'
    )

    # charts row
    nb = charts.get("native_bypass") or {}
    chart_blocks = []
    if charts.get("tool_use_top"):
        chart_blocks.append(bar_chart(charts["tool_use_top"], "Tool use", top=8, color=BAR))
    if nb.get("bypass_total") is not None and nb.get("native_total") is not None:
        chart_blocks.append(donut(nb["bypass_total"], nb["native_total"]))
    if charts.get("bash_verbs_top"):
        chart_blocks.append(bar_chart(charts["bash_verbs_top"], "Top bash verbs", top=10, color="#0ea5e9"))
    charts_html = (
        f'<h2 class="sec">Your activity</h2><div class="charts">{"".join(chart_blocks)}</div>'
        if chart_blocks else ""
    )

    recap_html = (
        '<h2 class="sec">What you\'ve been working on</h2>' + recap_strip(work_recap)
        if work_recap.get("top_projects") else ""
    )
    usage_html = (
        '<h2 class="sec">Usage &amp; cost</h2>' + usage_card(usage)
        if usage.get("totals") else ""
    )

    recs_html = (
        '<h2 class="sec">Recommendations</h2>' + "".join(rec_card(r) for r in recs)
        if recs else ""
    )
    gaps_html = (
        '<h2 class="sec">Gaps — worth authoring</h2><ul class="gaps">'
        + "".join(gap_item(g) for g in gaps) + "</ul>"
        if gaps else ""
    )
    coach_html = (
        '<h2 class="sec">Habits &amp; leverage — coaching</h2>'
        + "".join(coach_card(c) for c in coaching)
        if coaching else ""
    )

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>skills-daimon report</title><style>" + CSS + "</style></head><body>"
        '<div class="wrap">' + hero + recap_html + usage_html + charts_html + recs_html + gaps_html + coach_html
        + '<footer>Generated by skills-daimon · evidence from your own Claude Code sessions · '
          'no data left this machine</footer>'
        "</div></body></html>"
    )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", "--stdin"):
        payload = json.loads(Path(sys.argv[1]).read_text())
    else:
        payload = json.loads(sys.stdin.read())

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
