#!/usr/bin/env python3
"""
skills-daimon finalizer: merge the live session's recommendations/gaps into the
staged payload, render the HTML report, and append the history snapshot — one
call instead of three.

Usage:
    python3 finalize.py --fill fill.json
    python3 finalize.py --fill - < fill.json     # stdin

`fill.json` shape (both keys optional; everything else comes from run.py's
staged payload):
    {"recommendations": [{rank, confidence, type, name, job, evidence,
                          description, install:[...], source_url}],
     "gaps": [{tag, note, init}]}

Reads payload.json + snapshot.json from run.py's workdir (or --workdir).
Prints: {"url": "file://...", "path": "...", "history": "stored"}
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

BIN = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="skills-daimon finalize (merge+render+history)")
    ap.add_argument("--fill", required=True, help="recommendations/gaps JSON path, or - for stdin")
    ap.add_argument("--workdir", default=str(Path(tempfile.gettempdir()) / "skills-daimon-work"))
    args = ap.parse_args()

    workdir = Path(args.workdir)
    payload_path = workdir / "payload.json"
    snapshot_path = workdir / "snapshot.json"
    if not payload_path.exists():
        print(json.dumps({"error": f"missing {payload_path} — run run.py first"}))
        return 1

    fill_text = sys.stdin.read() if args.fill == "-" else Path(args.fill).read_text()
    fill = json.loads(fill_text)

    payload = json.loads(payload_path.read_text())
    payload["recommendations"] = fill.get("recommendations") or []
    payload["gaps"] = fill.get("gaps") or []
    payload_path.write_text(json.dumps(payload))

    py = sys.executable or "python3"
    r = subprocess.run([py, str(BIN / "render_report.py"), str(payload_path)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(json.dumps({"error": f"render_report failed: {r.stderr.strip()[:300]}"}))
        return 1
    rendered = json.loads(r.stdout)

    history = "skipped"
    if snapshot_path.exists():
        h = subprocess.run([py, str(BIN / "history.py"), "append", str(snapshot_path)],
                           capture_output=True, text=True, timeout=60)
        history = "stored" if h.returncode == 0 else f"failed: {h.stderr.strip()[:200]}"

    print(json.dumps({"url": rendered.get("url"), "path": rendered.get("path"),
                      "history": history}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
