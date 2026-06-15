"""Tests for the Codex session adapter — parses rollout JSONL into scan schema."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN))

import scan_codex


def _rollout(lines):
    return "\n".join(json.dumps(x) for x in lines)


def _ri(ptype, **p):
    return {"timestamp": "2026-06-10T00:00:00Z", "type": "response_item",
            "payload": {"type": ptype, **p}}


def _em(ptype, **p):
    return {"timestamp": "2026-06-10T00:00:00Z", "type": "event_msg",
            "payload": {"type": ptype, **p}}


SESSION = [
    {"type": "session_meta", "payload": {"cwd": "/Users/x/Code/proj"}},
    _em("user_message", message="add a retry to the fetch helper"),
    _ri("function_call", name="exec_command",
        arguments=json.dumps({"cmd": "git push --force origin main"}),
        call_id="c1"),
    _ri("function_call_output", call_id="c1", output="Process exited with code 0\n"),
    _ri("function_call", name="exec_command",
        arguments=json.dumps({"cmd": "pytest -q"}), call_id="c2"),
    _ri("function_call_output", call_id="c2", output="exited with code 1\n"),
    _ri("custom_tool_call", name="apply_patch", input="*** Begin Patch ..."),
    _em("patch_apply_end", changes={"/Users/x/Code/proj/a.py": {}}),
    _em("token_count", info={"total_token_usage": {"total_tokens": 1200}}),
    _em("token_count", info={"total_token_usage": {"total_tokens": 3400}}),
    _em("mcp_tool_call_end", tool="context-a8c"),
    {"type": "response_item", "payload": {"type": "message", "role": "user",
     "content": [{"type": "input_text",
                  "text": "The following is the Codex agent history ..."}]}},
]


class TestScanCodex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        d = root / "sessions" / "2026" / "06" / "10"
        d.mkdir(parents=True)
        (d / "rollout-2026-06-10T00-00-00-aaaa.jsonl").write_text(_rollout(SESSION))
        self.out = scan_codex.scan_codex(root, max_age_days=3650)

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_count(self):
        self.assertEqual(self.out["session_count"], 1)

    def test_schema_has_all_analyze_keys(self):
        for k in ("session_count", "bash_verbs_top", "tool_use_top",
                  "recurring_prompts", "coaching_signals", "outcomes",
                  "completion", "tool_errors", "memory_events", "stuck_loops",
                  "available_catalogs", "work_recap", "projects"):
            self.assertIn(k, self.out)

    def test_exec_command_becomes_bash(self):
        self.assertEqual(self.out["tool_use_top"].get("Bash"), 2)
        self.assertIn("git push", self.out["bash_verbs_top"])

    def test_error_rate_from_exit_codes(self):
        self.assertEqual(self.out["tool_errors"]["Bash"], {"ok": 1, "error": 1})

    def test_destructive_detected(self):
        labels = [d["label"] for d in self.out["coaching_signals"]["destructive_cmds"]]
        self.assertIn("git push --force", labels)

    def test_tokens_take_cumulative_max(self):
        self.assertEqual(self.out["work_recap"]["top_projects"][0]["tokens"], 3400)

    def test_real_prompt_kept_codex_noise_dropped(self):
        prompts = " ".join(p["prompt"] for p in self.out["recurring_prompts"])
        # only one session so counts are 1 (< 2) -> recurring list may be empty;
        # assert the noise never leaks regardless.
        self.assertNotIn("Codex agent history", prompts)

    def test_apply_patch_is_edit_and_files_counted(self):
        self.assertEqual(self.out["tool_use_top"].get("Edit"), 1)
        self.assertEqual(self.out["completion"]["files_modified"], 1)

    def test_no_facets_degrades_cleanly(self):
        self.assertEqual(self.out["outcomes"]["coverage"]["labeled"], 0)
        # native bypass zeroed so "shell vs built-in" reads no_data, not 100%.
        self.assertEqual(self.out["coaching_signals"]["native_tool_bypass"]["bash_total"], 0)

    def test_commit_push_detected(self):
        self.assertEqual(self.out["completion"]["sessions_with_push"], 1)


class TestRicherSignals(unittest.TestCase):
    """The medium-batch additions: work mix, lines, stuck loops."""
    def _scan(self, lines):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name) / "sessions" / "2026" / "06" / "10"
        d.mkdir(parents=True)
        (d / "rollout-z.jsonl").write_text(_rollout(lines))
        return scan_codex.scan_codex(Path(tmp.name), max_age_days=3650)

    def test_lines_added_removed_from_patch(self):
        patch = "*** Begin Patch\n+new line one\n+new line two\n-old line\n context\n*** End Patch"
        out = self._scan([
            {"type": "session_meta", "payload": {"cwd": "/p"}},
            _ri("custom_tool_call", name="apply_patch", input=patch),
        ])
        self.assertEqual(out["completion"]["lines_added"], 2)
        self.assertEqual(out["completion"]["lines_removed"], 1)

    def test_writing_mix_from_md_edits(self):
        out = self._scan([
            {"type": "session_meta", "payload": {"cwd": "/p"}},
            _ri("custom_tool_call", name="apply_patch", input="+x"),
            _em("patch_apply_end", changes={"/p/notes.md": {}, "/p/README.md": {}}),
        ])
        self.assertGreater(out["work_recap"]["mix"]["writing"], 0)
        self.assertEqual(out["work_recap"]["top_projects"][0]["kind"], "writing")

    def test_data_mix_from_mcp(self):
        out = self._scan([
            {"type": "session_meta", "payload": {"cwd": "/p"}},
            _em("mcp_tool_call_end", tool="trino_execute_sql"),
            _em("mcp_tool_call_end", tool="trino_query"),
        ])
        self.assertGreater(out["work_recap"]["mix"]["data"], 0)

    def test_stuck_loop_detected(self):
        def fc(ts):
            return {"timestamp": ts, "type": "response_item",
                    "payload": {"type": "function_call", "name": "exec_command",
                                "arguments": json.dumps({"cmd": "npm test"})}}
        out = self._scan([
            {"type": "session_meta", "payload": {"cwd": "/p"}},
            fc("2026-06-10T00:00:00Z"), fc("2026-06-10T00:00:30Z"),
            fc("2026-06-10T00:01:00Z"),
        ])
        self.assertEqual(len(out["stuck_loops"]), 1)
        self.assertEqual(out["stuck_loops"][0]["count"], 3)

    def test_honest_polling_not_flagged(self):
        # identical cmds but >2 min apart -> not a stuck loop.
        def fc(ts):
            return {"timestamp": ts, "type": "response_item",
                    "payload": {"type": "function_call", "name": "exec_command",
                                "arguments": json.dumps({"cmd": "gh run watch"})}}
        out = self._scan([
            {"type": "session_meta", "payload": {"cwd": "/p"}},
            fc("2026-06-10T00:00:00Z"), fc("2026-06-10T00:05:00Z"),
            fc("2026-06-10T00:10:00Z"),
        ])
        self.assertEqual(out["stuck_loops"], [])


class TestSourceAwareInstall(unittest.TestCase):
    def test_codex_flags_marketplace_nonportable(self):
        import catalog_search as cs
        mp = [{"name": "exa", "catalog_type": "marketplace",
               "source_url": "https://exa.ai",
               "install": ["/plugin install exa@x"], "matched_terms": ["w"]}]
        out = cs._apply_source([dict(c) for c in mp], "codex")
        self.assertFalse(out[0]["portable"])
        self.assertIn("install in Codex", out[0]["install"][0])

    def test_codex_keeps_skills_sh_portable(self):
        import catalog_search as cs
        reg = [{"name": "x", "catalog_type": "cli-registry",
                "install": ["npx skills add a/b@x"], "matched_terms": ["w"]}]
        out = cs._apply_source([dict(c) for c in reg], "codex")
        self.assertTrue(out[0]["portable"])
        self.assertEqual(out[0]["install"], ["npx skills add a/b@x"])

    def test_claude_unchanged(self):
        import catalog_search as cs
        mp = [{"name": "exa", "catalog_type": "marketplace",
               "install": ["/plugin install exa@x"], "matched_terms": ["w"]}]
        out = cs._apply_source([dict(c) for c in mp], "claude")
        self.assertTrue(out[0]["portable"])
        self.assertEqual(out[0]["install"], ["/plugin install exa@x"])


class TestNoiseFilter(unittest.TestCase):
    def test_codex_preamble_detected(self):
        self.assertTrue(scan_codex._is_codex_noise("The following is the Codex agent history\n..."))
        self.assertTrue(scan_codex._is_codex_noise("<permissions instructions>\n"))
        self.assertFalse(scan_codex._is_codex_noise("fix the failing test"))


class TestPipelineCompat(unittest.TestCase):
    def test_analyze_consumes_codex_scan(self):
        import analyze
        out = analyze.analyze(self.scan)
        self.assertIn("verdict", out)
        self.assertIn("markdown_report", out)
        # outcome-based scorecard row degrades to no_data, never crashes.
        labels = {r["label"]: r["verdict"] for r in out["scorecard"]}
        self.assertEqual(labels.get("Sessions finished"), "no_data")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        d = root / "sessions" / "2026" / "06" / "10"
        d.mkdir(parents=True)
        (d / "rollout-x.jsonl").write_text(_rollout(SESSION))
        self.scan = scan_codex.scan_codex(root, max_age_days=3650)

    def tearDown(self):
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
