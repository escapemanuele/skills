"""Tests for the token-reduction section: deterministic tips + HTML block."""
import sys
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import analyze
import render_report as rr


def _metrics(**over):
    m = {
        "bypass_total": 0, "bash_total": 0, "bypass_pct": 0, "bypass_calls": {},
        "stuck_loops": [], "bash_error": 0, "bash_ok": 0, "bash_error_pct": 0,
    }
    m.update(over)
    return m


class TestBuildTokenTips(unittest.TestCase):
    def test_empty_when_no_signal(self):
        self.assertEqual(analyze.build_token_tips(_metrics()), [])

    def test_shell_search_tip_gated_and_counted(self):
        m = _metrics(bypass_total=440, bash_total=2351, bypass_pct=19,
                     bypass_calls={"grep": 243, "cat": 93, "find": 67})
        tips = analyze.build_token_tips(m)
        self.assertEqual(len(tips), 1)
        self.assertIn("440 of 2351", tips[0]["evidence"])
        self.assertIn("grep×243", tips[0]["evidence"])  # top breakdown
        self.assertTrue(tips[0]["tip"])

    def test_shell_search_below_threshold_skipped(self):
        m = _metrics(bypass_total=19, bash_total=100, bypass_pct=19)
        self.assertEqual(analyze.build_token_tips(m), [])

    def test_stuck_loop_tip(self):
        m = _metrics(stuck_loops=[{"count": 5}, {"count": 3}])
        tips = analyze.build_token_tips(m)
        self.assertEqual(len(tips), 1)
        self.assertIn("2 stuck loops", tips[0]["evidence"])
        self.assertIn("×5", tips[0]["evidence"])

    def test_singular_loop_wording(self):
        m = _metrics(stuck_loops=[{"count": 4}])
        self.assertIn("1 stuck loop;", analyze.build_token_tips(m)[0]["evidence"])

    def test_bash_error_tip_gated(self):
        self.assertEqual(analyze.build_token_tips(_metrics(bash_error=39, bash_ok=10)), [])
        tips = analyze.build_token_tips(_metrics(bash_error=127, bash_ok=2220,
                                                 bash_error_pct=5))
        self.assertEqual(len(tips), 1)
        self.assertIn("127 of 2347", tips[0]["evidence"])

    def test_cap_three(self):
        m = _metrics(bypass_total=440, bash_total=2351, bypass_pct=19,
                     bypass_calls={"grep": 243},
                     stuck_loops=[{"count": 5}],
                     bash_error=127, bash_ok=2220, bash_error_pct=5)
        self.assertEqual(len(analyze.build_token_tips(m)), 3)


class TestTokenBlockHtml(unittest.TestCase):
    def test_empty_renders_nothing(self):
        self.assertEqual(rr.simple_token_block([]), "")

    def test_renders_section_with_evidence(self):
        html = rr.simple_token_block([
            {"title": "Search with the built-in tools",
             "evidence": "440 of 2351 bash calls were shell search/read (19%)",
             "tip": "Use Grep/Glob/Read."}])
        self.assertIn("💸 Trim token usage", html)
        self.assertIn("440 of 2351", html)
        self.assertIn("Use Grep/Glob/Read.", html)

    def test_escapes(self):
        html = rr.simple_token_block([{"title": "A & B", "evidence": "<x>", "tip": "t"}])
        self.assertIn("&amp;", html)
        self.assertNotIn("<x>", html)


class TestAnalyzeIntegration(unittest.TestCase):
    def test_token_tips_in_payload_and_markdown(self):
        scan = {
            "session_count": 100, "max_age_days": 28,
            "coaching_signals": {"native_tool_bypass": {
                "bash_total": 2351, "bypass_total": 440,
                "bypass_calls": {"grep": 243, "cat": 93}}},
            "stuck_loops": [{"count": 5}],
            "tool_errors": {"Bash": {"ok": 2220, "error": 127}},
        }
        out = analyze.analyze(scan)
        self.assertTrue(out["token_tips"])
        self.assertIn("token_tips", out["html_payload"])
        self.assertIn("💸 Trim token usage", out["markdown_report"])


if __name__ == "__main__":
    unittest.main()
