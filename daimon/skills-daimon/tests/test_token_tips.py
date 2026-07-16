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


class TestBuildTokenSavings(unittest.TestCase):
    def _m(self, **over):
        m = {"bypass_saved_tokens": 0, "error_waste_tokens": 0,
             "bypass_out_tokens": 0, "bypass_native_est_tokens": 0,
             "window_tokens": 0}
        m.update(over)
        return m

    def test_none_when_below_threshold(self):
        self.assertIsNone(analyze.build_token_savings(self._m(bypass_saved_tokens=500)))

    def test_headline_with_bypass_and_errors(self):
        s = analyze.build_token_savings(self._m(
            bypass_saved_tokens=50_000, bypass_out_tokens=60_000,
            bypass_native_est_tokens=10_000, error_waste_tokens=7_000,
            window_tokens=2_700_000))
        self.assertEqual(s["estimated_saved_tokens"], 57_000)
        self.assertEqual(s["pct_of_window"], 2)
        self.assertIn("~57k tokens", s["headline"])
        self.assertIn("60k tokens", s["headline"])
        self.assertIn("~10k", s["headline"])
        self.assertIn("7k", s["headline"])

    def test_no_pct_without_window(self):
        s = analyze.build_token_savings(self._m(error_waste_tokens=5_000))
        self.assertNotIn("pct_of_window", s)
        self.assertIn("~5k tokens", s["headline"])


class TestSavingsMetrics(unittest.TestCase):
    def test_metrics_compute_savings_from_measured_chars(self):
        scan = {
            "session_count": 10,
            "coaching_signals": {
                "native_tool_bypass": {
                    "bash_total": 100, "bypass_total": 50,
                    "bypass_calls": {"grep": 50},
                    "bypass_result_chars": 400_000,     # → 100k tokens
                    "bypass_results_measured": 50,
                    "native_result_chars": 40_000,      # → 10k tokens over 20 calls
                    "native_results_measured": 20,      # avg 500 tokens/call
                },
                "bash_error_chars": 8_000,              # → 2k tokens
            },
            "work_recap": {"top_projects": [{"tokens": 1_000_000}]},
        }
        m = analyze.compute_metrics(scan)
        self.assertEqual(m["bypass_out_tokens"], 100_000)
        self.assertEqual(m["bypass_native_est_tokens"], 25_000)  # 50 × 500
        self.assertEqual(m["bypass_saved_tokens"], 75_000)
        self.assertEqual(m["error_waste_tokens"], 2_000)
        self.assertEqual(m["window_tokens"], 1_000_000)

    def test_fallback_native_avg_and_zero_floor(self):
        scan = {
            "session_count": 10,
            "coaching_signals": {
                "native_tool_bypass": {
                    "bash_total": 100, "bypass_total": 50,
                    "bypass_calls": {"grep": 50},
                    "bypass_result_chars": 40_000,      # → 10k tokens
                    "bypass_results_measured": 50,
                    "native_result_chars": 0,
                    "native_results_measured": 0,       # < 10 → fallback 300/call
                },
            },
        }
        m = analyze.compute_metrics(scan)
        self.assertEqual(m["bypass_native_est_tokens"], 15_000)  # 50 × 300
        self.assertEqual(m["bypass_saved_tokens"], 0)            # floored, never negative


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

    def test_savings_banner_in_markdown_and_payload(self):
        scan = {
            "session_count": 100, "max_age_days": 28,
            "coaching_signals": {
                "native_tool_bypass": {
                    "bash_total": 2351, "bypass_total": 440,
                    "bypass_calls": {"grep": 243, "cat": 93},
                    "bypass_result_chars": 400_000,
                    "bypass_results_measured": 400,
                    "native_result_chars": 100_000,
                    "native_results_measured": 100,
                },
                "bash_error_chars": 20_000,
            },
            "work_recap": {"top_projects": [{"tokens": 2_000_000}]},
        }
        out = analyze.analyze(scan)
        self.assertIsNotNone(out["token_savings"])
        self.assertIn("Doing it the daimon way", out["markdown_report"])


class TestSavingsBannerHtml(unittest.TestCase):
    def test_banner_rendered_when_present(self):
        html = rr.simple_token_block(
            [{"title": "t", "evidence": "e", "tip": "x"}],
            {"headline": "Doing it the daimon way would have saved ~57k tokens."})
        self.assertIn("~57k tokens", html)

    def test_no_banner_without_savings(self):
        html = rr.simple_token_block([{"title": "t", "evidence": "e", "tip": "x"}], None)
        self.assertNotIn("daimon way", html)


if __name__ == "__main__":
    unittest.main()
