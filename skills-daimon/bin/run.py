#!/usr/bin/env python3
"""
skills-daimon one-shot orchestrator: scan -> analyze -> stage files, then print
a SLIM summary for the live session.

Replaces the first half of the pipeline (scan.py + analyze.py + payload
staging + trends lookup) with a single call, so the agent reads ~4KB instead
of the full ~33KB analysis JSON.

Usage:
    python3 run.py [--days 28] [--budget compact|normal|full]

Writes (workdir is printed in the output):
    scan.json       full scan
    analysis.json   full analyze.py output
    payload.json    html_payload skeleton (recommendations/gaps empty; the
                    live session fills them via finalize.py)
    snapshot.json   history snapshot (date stamped)

Prints one JSON object:
    {"gates": {"session_count": N, "catalogs": [names]},
     "paths": {...},
     "markdown_skeleton": "...",          # report skeleton from analyze.py
     "job_signals": {...},                # compact evidence for job clustering
     "trends": {"distinct_days": N, "rows": [{label, now, prev, direction}]}}

GATE A: if session_count == 0, prints {"gates": {"session_count": 0}} and
exits 0 — the live session stops there.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BIN = Path(__file__).resolve().parent

TREND_LABELS = {
    "outcome_finished_pct": ("Sessions finished", "%"),
    "risky_git_count": ("Risky git commands", ""),
    "search_shell_pct": ("Shell search share", "%"),
    "bash_error_pct": ("Bash error rate", "%"),
    "memory_rate_pct": ("Memory usage", "%"),
}


def _run_json(cmd: list[str], stdin_text: str | None = None) -> dict | list:
    r = subprocess.run(cmd, capture_output=True, text=True, input=stdin_text, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[1]}: exit {r.returncode}: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout)


def _truncate_prompts(prompts: list[dict], n: int = 8, width: int = 90) -> list[dict]:
    return [{"prompt": (p.get("prompt") or "")[:width], "count": p.get("count")}
            for p in (prompts or [])[:n]]


def _top(d: dict | None, n: int) -> dict:
    return dict(list((d or {}).items())[:n])


def build_trends(snapshot: dict, history: list[dict]) -> dict:
    """Compare today's scorecard against the most recent PRIOR distinct date."""
    today = snapshot.get("date")
    dates = sorted({h.get("date") for h in history if h.get("date")})
    prior = [h for h in history if h.get("date") and h.get("date") != today]
    prev = prior[-1] if prior else None
    rows = []
    if prev:
        cur_sc = snapshot.get("scorecard") or {}
        prev_sc = prev.get("scorecard") or {}
        for key, (label, unit) in TREND_LABELS.items():
            now, old = cur_sc.get(key), prev_sc.get(key)
            if now is None or old is None:
                continue
            direction = "=" if now == old else ("up" if now > old else "down")
            rows.append({"label": label, "now": f"{now}{unit}",
                         "prev": old, "direction": direction})
    return {"distinct_days": len(dates), "prev_date": prev.get("date") if prev else None,
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description="skills-daimon orchestrator (scan+analyze+stage)")
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--budget", default="compact", choices=["compact", "normal", "full"])
    args = ap.parse_args()

    workdir = Path(tempfile.gettempdir()) / "skills-daimon-work"
    workdir.mkdir(parents=True, exist_ok=True)
    py = sys.executable or "python3"

    scan = _run_json([py, str(BIN / "scan.py"), "--days", str(args.days),
                      "--budget", args.budget])
    scan_path = workdir / "scan.json"
    scan_path.write_text(json.dumps(scan))

    # GATE A — no sessions: stop here, nothing else to stage.
    if not scan.get("session_count"):
        print(json.dumps({"gates": {"session_count": 0, "catalogs": []}}))
        return 0

    analysis = _run_json([py, str(BIN / "analyze.py"), str(scan_path)])
    (workdir / "analysis.json").write_text(json.dumps(analysis))

    today = datetime.date.today().isoformat()
    payload = analysis.get("html_payload") or {}
    payload.setdefault("meta", {})["date"] = today
    payload_path = workdir / "payload.json"
    payload_path.write_text(json.dumps(payload))

    snapshot = analysis.get("history_snapshot") or {}
    snapshot["date"] = today
    snapshot_path = workdir / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot))

    try:
        history = _run_json([py, str(BIN / "history.py"), "read", "--last", "6"])
    except (RuntimeError, ValueError):
        history = []

    out = {
        "gates": {
            "session_count": scan.get("session_count"),
            "catalogs": [c.get("name") for c in scan.get("available_catalogs") or []],
        },
        "paths": {
            "workdir": str(workdir),
            "scan": str(scan_path),
            "analysis": str(workdir / "analysis.json"),
            "payload": str(payload_path),
            "snapshot": str(snapshot_path),
        },
        "markdown_skeleton": analysis.get("markdown_report") or "",
        "job_signals": {
            "work_mix": (analysis.get("work_recap") or {}).get("mix"),
            "bash_verbs_top": _top(scan.get("bash_verbs_top"), 12),
            "mcp_calls_top": _top(scan.get("mcp_calls_top"), 8),
            "recurring_prompts": _truncate_prompts(scan.get("recurring_prompts")),
            "web_fetches": scan.get("web_fetches"),
        },
        "trends": build_trends(snapshot, history if isinstance(history, list) else []),
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
