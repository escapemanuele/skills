#!/usr/bin/env python3
"""
Smoke + unit tests for the skills-daimon refactor (scan budgets, analyze.py,
catalog_search.py, redact.py, history.py).

Run:
    python3 -m unittest -v skills-daimon.tests.test_refactor
    # or, from the skills-daimon dir:
    python3 -m unittest -v tests.test_refactor
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Make `import scan` etc. work when run from the repo or as a package.
BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import analyze            # noqa: E402
import catalog_search     # noqa: E402
import history as history_mod  # noqa: E402
import redact             # noqa: E402
import render_report      # noqa: E402
import scan               # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_session(root: Path, project: str, sid: str, *, verbs, prompts):
    """Write a synthetic session jsonl with recent timestamps."""
    pdir = root / project
    pdir.mkdir(parents=True, exist_ok=True)
    lines = []
    ts = _now_iso()
    for p in prompts:
        lines.append({"type": "user", "timestamp": ts, "cwd": str(pdir),
                      "message": {"content": p}})
    for i, v in enumerate(verbs):
        lines.append({"type": "assistant", "timestamp": ts, "cwd": str(pdir),
                      "message": {"usage": {"input_tokens": 10, "output_tokens": 5},
                                  "content": [{"type": "tool_use", "id": f"t{i}",
                                               "name": "Bash",
                                               "input": {"command": v}}]}})
    f = pdir / f"{sid}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    return f


def _tok(prefix: str, body: str) -> str:
    """Assemble a fake token at runtime. The full token literal never appears
    contiguously in this source file, so GitHub push protection / secret
    scanners don't flag these test fixtures — but redact() still sees the real
    assembled shape at runtime."""
    return prefix + body


class TestRedaction(unittest.TestCase):
    SECRETS = {
        "jwt": _tok("eyJ", "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456"),
        "slack_bot": _tok("xoxb-", "123456789012-abcdEFGHijklMNOP"),
        "slack_app": _tok("xapp-", "1-A0123-456-abcdefghij"),
        "gitlab": _tok("glpat-", "abcd1234EFGH5678ijkl"),
        "npm": _tok("npm_", "abcdefghij1234567890ABCDEFGHIJ1234567890"),
        "google": _tok("AIza", "Sy01234567890abcdefghijklmnopqrstuvwx"),
        "github": _tok("ghp_", "abcdefghijklmnopqrstuvwxyz0123456"),
        "pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\nDEF456\n-----END RSA PRIVATE KEY-----",
    }

    def test_masks_known_token_shapes(self):
        for name, secret in self.SECRETS.items():
            with self.subTest(token=name):
                out = redact.redact(f"value is {secret} ok")
                self.assertIn("<REDACTED", out)
                self.assertNotIn(secret, out)

    def test_no_false_positive_on_plain_text_and_paths(self):
        for benign in ("the quick brown fox jumps over the lazy dog today",
                       "/Users/me/Code/skills/skills-daimon/bin/scan.py",
                       "git push origin main"):
            self.assertNotIn("<REDACTED", redact.redact(benign))

    def test_idempotent(self):
        s = f"token {self.SECRETS['jwt']} and {self.SECRETS['github']}"
        once = redact.redact(s)
        self.assertEqual(once, redact.redact(once))

    def test_redacts_dict_keys_too(self):
        out = redact.redact_in({self.SECRETS["github"]: "v"})
        self.assertNotIn(self.SECRETS["github"], out)
        self.assertEqual(list(out.values()), ["v"])


class TestHistoryNumericOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = history_mod.HISTORY_PATH
        history_mod.HISTORY_PATH = Path(self.tmp.name) / "history.jsonl"

    def tearDown(self):
        history_mod.HISTORY_PATH = self._orig
        self.tmp.cleanup()

    def test_drops_non_numeric_scorecard_and_command_text(self):
        snap = {
            "date": "2026-06-05", "window_days": 28, "sessions": 10, "labeled": 5,
            "scorecard": {"risky_git_count": 4, "leak": "rm -rf /important/path"},
            "archetype": "The Scribe", "work_mix": {"dev": 60},
            "game": {"xp_total": 12, "raw_quest": "do the thing"},
        }
        stored = history_mod.append(snap)
        self.assertEqual(stored["scorecard"], {"risky_git_count": 4})
        self.assertNotIn("leak", stored["scorecard"])
        self.assertEqual(stored["game"], {"xp_total": 12})
        blob = history_mod.HISTORY_PATH.read_text()
        self.assertNotIn("rm -rf", blob)
        self.assertNotIn("do the thing", blob)

    def test_same_day_overwrites(self):
        base = {"date": "2026-06-05", "window_days": 28, "sessions": 1, "labeled": 0,
                "scorecard": {"risky_git_count": 1}}
        history_mod.append(base)
        history_mod.append({**base, "scorecard": {"risky_git_count": 9}})
        entries = history_mod.read_last(10, window_days=28)
        same_day = [e for e in entries if e["date"] == "2026-06-05"]
        self.assertEqual(len(same_day), 1)
        self.assertEqual(same_day[0]["scorecard"]["risky_git_count"], 9)


class TestAnalyzeEvidenceBound(unittest.TestCase):
    def _scan_fixture(self, **over):
        base = {
            "session_count": 100, "max_age_days": 28, "projects": {"p": 100},
            "tool_use_top": {"Bash": 50}, "bash_verbs_top": {}, "available_catalogs": [],
            "installed_skills": [], "installed_plugins": [], "recurring_prompts": [],
            "coaching_signals": {"native_tool_bypass": {"bash_total": 0, "bypass_total": 0,
                                 "bypass_calls": {}, "native_tool_use": {}},
                                 "destructive_cmds": [], "raw_http_hosts": {},
                                 "sleep_calls": 0, "hot_repos_without_claudemd": []},
            "outcomes": {"by_facet": {"fully_achieved": 5, "mostly_achieved": 3},
                         "friction_sessions": {}, "coverage": {"labeled": 10, "total": 100}},
            "memory_events": {"sessions_with_memory": 0}, "tool_errors": {},
            "work_recap": {"mix": {}, "top_projects": []}, "stuck_loops": [],
            "completion": {},
        }
        base.update(over)
        return base

    def test_never_invents_recommendations_with_empty_catalogs(self):
        out = analyze.analyze(self._scan_fixture())
        self.assertEqual(out["recommendations"], [])
        self.assertEqual(out["gaps"], [])
        self.assertIn("RECOMMENDATIONS:", out["markdown_report"])

    def test_outcome_rate_uses_labeled_not_total(self):
        m = analyze.compute_metrics(self._scan_fixture())
        # (5 + 3) / 10 labeled = 80%, NOT 8/100
        self.assertEqual(m["finished_pct"], 80)
        self.assertEqual(m["labeled"], 10)

    def test_memory_rate_uses_session_count(self):
        m = analyze.compute_metrics(self._scan_fixture(
            session_count=200, memory_events={"sessions_with_memory": 10}))
        self.assertEqual(m["memory_rate_pct"], 5)  # 10/200

    def test_risky_git_excludes_rm_rf(self):
        sc = self._scan_fixture()
        sc["coaching_signals"]["destructive_cmds"] = [
            {"label": "rm -rf", "count": 9, "sample": "rm -rf /tmp/x"},
            {"label": "git push --force", "count": 2, "sample": "git push -f"},
        ]
        m = analyze.compute_metrics(sc)
        self.assertEqual(m["risky_git"], 2)  # rm -rf ignored

    def test_history_snapshot_numeric_only(self):
        out = analyze.analyze(self._scan_fixture())
        sc = out["history_snapshot"]["scorecard"]
        self.assertTrue(all(isinstance(v, (int, float)) for v in sc.values()))
        self.assertTrue(all(isinstance(v, (int, float)) for v in out["history_snapshot"]["game"].values()))


class TestCatalogSearch(unittest.TestCase):
    def test_empty_catalogs_yields_no_candidates(self):
        out = catalog_search.search(
            {"available_catalogs": [], "installed_skills": [], "ignored_names": []},
            ["anything"])
        self.assertEqual(out["candidates"], [])

    def test_marketplace_match_and_install_command(self):
        with tempfile.TemporaryDirectory() as d:
            mj = Path(d) / "marketplace.json"
            mj.write_text(json.dumps({"plugins": [
                {"name": "pr-review-toolkit", "description": "review pull requests",
                 "homepage": "https://example.com/pr"},
                {"name": "unrelated", "description": "make coffee"},
            ]}))
            hits = catalog_search.search_marketplace(str(mj), "mp", ["pull request review"])
        self.assertEqual([h["name"] for h in hits], ["pr-review-toolkit"])
        self.assertEqual(hits[0]["install"], ["/plugin install pr-review-toolkit@mp"])
        self.assertEqual(hits[0]["source_url"], "https://example.com/pr")

    def test_dedupe_prefers_registry_and_drops_installed(self):
        hits = [
            {"name": "foo", "catalog_type": "marketplace", "matched_terms": ["x"]},
            {"name": "foo", "catalog_type": "cli-registry", "matched_terms": ["y"]},
            {"name": "bar", "catalog_type": "marketplace", "matched_terms": ["x"]},
        ]
        out = catalog_search.dedupe_and_filter(hits, installed={"bar"}, ignored=set())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "foo")
        self.assertEqual(out[0]["catalog_type"], "cli-registry")
        self.assertEqual(out[0]["matched_terms"], ["x", "y"])  # merged


class TestScanBudgets(unittest.TestCase):
    def _fixture_root(self, d: Path):
        verbs = [f"tool{i} sub run --flag" for i in range(30)]
        for s in range(5):
            # Unique prompts per session so they register as one-offs (count==1),
            # not recurring — exercises the oneoff_count budget knob.
            prompts = [f"session {s} unique task number {i} now" for i in range(40)]
            _write_session(d, "projectA", f"sess-{s}", verbs=verbs, prompts=prompts)
        return d

    def test_compact_smaller_than_full_with_identical_keys(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._fixture_root(Path(td))
            compact = scan.scan(root, 28, cwd=root, budget="compact")
            full = scan.scan(root, 28, cwd=root, budget="full")
        cb, fb = len(json.dumps(compact)), len(json.dumps(full))
        self.assertLess(cb, fb, f"compact {cb} not < full {fb}")
        self.assertEqual(set(compact.keys()), set(full.keys()))
        # compact disables one-offs and session index
        self.assertEqual(compact["sampled_oneoff_prompts"], [])
        self.assertEqual(compact["session_index"], [])
        self.assertGreater(len(full["sampled_oneoff_prompts"]), 0)

    def test_event_time_filter_excludes_old_events(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdir = root / "projectOld"
            pdir.mkdir(parents=True)
            old_ts = "2000-01-01T00:00:00+00:00"
            ev = {"type": "assistant", "timestamp": old_ts, "cwd": str(pdir),
                  "message": {"content": [{"type": "tool_use", "id": "x", "name": "Bash",
                                           "input": {"command": "grep foo"}}]}}
            (pdir / "old.jsonl").write_text(json.dumps(ev))
            # File mtime is "now" (just written) so it passes the pre-filter,
            # but the event itself is from 2000 and must be dropped.
            out = scan.scan(root, 28, cwd=root, budget="full")
        self.assertEqual(out["bash_verbs_top"], {})
        self.assertEqual(out["coaching_signals"]["native_tool_bypass"]["bash_total"], 0)


class TestNoRawCommandsInHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = history_mod.HISTORY_PATH
        history_mod.HISTORY_PATH = Path(self.tmp.name) / "history.jsonl"

    def tearDown(self):
        history_mod.HISTORY_PATH = self._orig
        self.tmp.cleanup()

    def test_stuck_loop_command_never_reaches_history(self):
        scan_fixture = {
            "session_count": 1, "max_age_days": 28, "projects": {"p": 1},
            "tool_use_top": {}, "bash_verbs_top": {}, "available_catalogs": [],
            "installed_skills": [], "installed_plugins": [], "recurring_prompts": [],
            "coaching_signals": {"native_tool_bypass": {"bash_total": 0, "bypass_total": 0,
                                 "bypass_calls": {}, "native_tool_use": {}},
                                 "destructive_cmds": [], "raw_http_hosts": {},
                                 "sleep_calls": 0, "hot_repos_without_claudemd": []},
            "outcomes": {"by_facet": {}, "friction_sessions": {}, "coverage": {"labeled": 0, "total": 1}},
            "memory_events": {"sessions_with_memory": 0}, "tool_errors": {},
            "work_recap": {"mix": {}, "top_projects": []}, "completion": {},
            "stuck_loops": [{"command_hash": "abc", "command_summary": "psql secret …",
                             "command": "psql 'password=hunter2 token=tok_xyzzy'",
                             "count": 5, "session": "s1"}],
        }
        out = analyze.analyze(scan_fixture)
        snap = out["history_snapshot"]
        snap["date"] = "2026-06-05"  # caller stamps today's date in the pipeline
        history_mod.append(snap)
        blob = history_mod.HISTORY_PATH.read_text()
        # Raw command text, its arguments, and the redacted summary must all be
        # absent from numeric-only history. (Numeric field names like
        # "grove_command_tree_level" are fine — only raw command DATA is barred.)
        self.assertNotIn("psql", blob)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("tok_xyzzy", blob)
        self.assertNotIn("secret", blob)
        self.assertNotIn("command_hash", blob)
        self.assertNotIn("command_summary", blob)


class TestSimpleView(unittest.TestCase):
    def _payload(self, recs):
        return {
            "meta": {"days": 28, "date": "2026-06-05"},
            "archetype": {"title": "The Builder-Scribe", "tagline": "t"},
            "primary_action": {"title": "Reach for safer git defaults",
                               "phrase": "use --force-with-lease", "why": "66 risky commands.",
                               "source": "Behavior recommendation"},
            "recommendations": recs,
            "coaching": [{"title": "Searching the hard way", "evidence": "491 of 2558 calls",
                          "costs": "Built-ins are cheaper.", "better": "Use Grep/Glob/Read."}],
            "scorecard": [], "work_recap": {}, "charts": {},
        }

    def test_simple_default_advanced_hidden_with_toggle(self):
        html = render_report.render(self._payload([
            {"rank": 1, "confidence": "high", "type": "plugin", "name": "code-review",
             "job": "PR review", "description": "Automated PR review.",
             "install": ["/plugin install code-review@mp"], "source_url": "https://x/cr"},
        ]))
        self.assertIn('id="view-simple"', html)
        self.assertIn('id="view-advanced" style="display:none"', html)
        self.assertLess(html.index('id="view-simple"'), html.index('id="view-advanced"'))
        self.assertIn("function sdToggleView", html)
        self.assertEqual(html.count('onclick="sdToggleView()"'), 2)
        # simple sections present + the action/rec/coaching content
        for needle in ("Do this next", "Recommended for you", "Habits to tweak",
                       "code-review", "/plugin install code-review@mp", "Searching the hard way"):
            self.assertIn(needle, html)
        # hero archetype title (just the title) appears in the simple view
        self.assertIn('class="simple-arch-title">The Builder-Scribe', html)

    def test_simple_view_notes_when_fewer_than_three_recs(self):
        html = render_report.render(self._payload([
            {"rank": 1, "confidence": "high", "type": "plugin", "name": "a",
             "install": ["x"], "source_url": ""},
            {"rank": 2, "confidence": "med", "type": "plugin", "name": "b",
             "install": ["y"], "source_url": ""},
        ]))
        self.assertIn("Only 2 catalog-backed match", html)

    def test_no_external_resource_loads(self):
        html = render_report.render(self._payload([]))
        import re
        self.assertEqual(
            re.findall(r'(?:<script[^>]+src|<link[^>]+href|<img[^>]+src)="https?://', html), [])


if __name__ == "__main__":
    unittest.main()
