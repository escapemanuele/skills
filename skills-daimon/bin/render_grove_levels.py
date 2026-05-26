#!/usr/bin/env python3
"""Showcase every track-level combination of the Daimon Grove SVG.

Writes a static HTML file that renders the grove at:
- all six sites at the same level (L0..L6),
- per-track walks (each track L1..L6 while others stay at L1),
- the balanced grove (every track >= L2 → night sky + constellation),
- a "max grove" (every track at L6).

Output: ~/.claude/skills/skills-daimon/reports/grove-levels.html (override
with --out PATH).

Used to eyeball the layout when changing render_grove_svg in gamify.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from bin.gamify import render_grove_svg  # noqa: E402


TRACK_KEYS = (
    "command_tree_level",
    "memory_well_level",
    "git_thorn_level",
    "planning_path_level",
    "tool_shrine_level",
    "repo_signpost_level",
)

TRACK_DISPLAY = {
    "command_tree_level":  "Command Tree",
    "memory_well_level":   "Memory Well",
    "git_thorn_level":     "Git Thorns",
    "planning_path_level": "Planning Path",
    "tool_shrine_level":   "Tool Shrine",
    "repo_signpost_level": "Repo Signpost",
}


def grove(levels: dict, *, balanced: bool | None = None,
          constellation: bool = True) -> dict:
    track_levels = [int(levels.get(k, 0)) for k in TRACK_KEYS]
    auto_balanced = all(lvl >= 2 for lvl in track_levels)
    return {
        "level": max(1, sum(track_levels) // 6),
        **{k: int(levels.get(k, 0)) for k in TRACK_KEYS},
        "tracks_at_l2_or_more": sum(1 for lvl in track_levels if lvl >= 2),
        "balanced": auto_balanced if balanced is None else balanced,
        "constellation_unlocked": constellation,
    }


def section(title: str, blurb: str, items: list[tuple[str, dict]]) -> str:
    cards = []
    for caption, g in items:
        cards.append(
            f'<figure class="card">'
            f'<figcaption><b>{caption}</b><span class="meta">'
            f'tracks≥L2: {g["tracks_at_l2_or_more"]} · balanced: {str(g["balanced"]).lower()}'
            f'</span></figcaption>'
            f'<div class="map">{render_grove_svg(g)}</div>'
            f'</figure>'
        )
    return (
        f'<section><h2>{title}</h2><p class="blurb">{blurb}</p>'
        f'<div class="grid">{"".join(cards)}</div></section>'
    )


def build_html() -> str:
    uniform_rows = [
        (f"Every track at Level {lvl}",
         grove({k: lvl for k in TRACK_KEYS}))
        for lvl in range(0, 7)
    ]

    walk_sections = []
    for key in TRACK_KEYS:
        rows = []
        baseline = {k: 1 for k in TRACK_KEYS}
        for lvl in range(1, 7):
            levels = dict(baseline)
            levels[key] = lvl
            rows.append(
                (f"{TRACK_DISPLAY[key]} → Level {lvl}", grove(levels))
            )
        walk_sections.append(section(
            f"{TRACK_DISPLAY[key]} solo walk (others stay at L1)",
            "Only this track changes — confirms the glyph reads its level "
            "in isolation and the others stay put.",
            rows,
        ))

    extras = [
        ("Just-balanced (all L2)",
         grove({k: 2 for k in TRACK_KEYS})),
        ("Half-balanced (3 of 6 ≥ L2, not yet lit)",
         grove({
            "command_tree_level": 1, "memory_well_level": 3,
            "git_thorn_level": 1, "planning_path_level": 4,
            "tool_shrine_level": 1, "repo_signpost_level": 2,
         })),
        ("Mixed real-world snapshot",
         grove({
            "command_tree_level": 1, "memory_well_level": 2,
            "git_thorn_level": 1, "planning_path_level": 1,
            "tool_shrine_level": 1, "repo_signpost_level": 1,
         })),
        ("Max grove (every track L6)",
         grove({k: 6 for k in TRACK_KEYS})),
    ]

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Daimon Grove · Level Showcase</title>"
        "<style>"
        "body{margin:0;background:#F8F7F3;font:14px/1.45 -apple-system,sans-serif;color:#171717}"
        ".wrap{max-width:1080px;margin:0 auto;padding:32px 24px 64px}"
        "h1{font-size:22px;font-weight:800;margin:0 0 6px}"
        ".sub{color:#6B7280;margin:0 0 28px}"
        "section{margin:0 0 36px}"
        "h2{font-size:16px;font-weight:700;margin:0 0 4px;color:#2F271F}"
        ".blurb{color:#6B7280;margin:0 0 14px;font-size:13px}"
        ".grid{display:grid;grid-template-columns:1fr;gap:18px}"
        ".card{margin:0;background:#fffdf8;border:1px solid #e7d6b6;border-radius:12px;overflow:hidden}"
        "figcaption{padding:10px 14px;border-bottom:1px solid #ead6b5;background:#fff4dc;"
        "display:flex;justify-content:space-between;align-items:baseline;gap:12px;font-size:13px;color:#2F271F}"
        ".meta{color:#6B5A46;font-size:11px;font-family:ui-monospace,monospace}"
        ".map{padding:8px}"
        ".map svg{display:block;width:100%;height:auto}"
        "</style></head><body><div class='wrap'>"
        "<h1>🌲 Daimon Grove · Level Showcase</h1>"
        "<p class='sub'>Every glyph rendered across its range so layout changes are easy to eyeball. "
        "Regenerate via <code>python3 skills-daimon/bin/render_grove_levels.py</code>.</p>"
        + section(
            "Uniform levels (all six tracks equal)",
            "Same level across all sites — checks horizontal spacing, ground line, "
            "and the day→night transition at L2.",
            uniform_rows,
        )
        + section(
            "Edge cases & realistic mixes",
            "These are the most common shapes a real report will hit.",
            extras,
        )
        + "".join(walk_sections)
        + "</div></body></html>"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    default_out = Path.home() / ".claude" / "skills" / "skills-daimon" / "reports" / "grove-levels.html"
    ap.add_argument("--out", type=Path, default=default_out,
                    help=f"output HTML path (default: {default_out})")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_html(), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
