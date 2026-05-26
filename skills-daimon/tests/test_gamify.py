#!/usr/bin/env python3
"""
Tests for gamify.py.

Self-contained, no external deps. Run with:
    python3 -m unittest -v skills-daimon.tests.test_gamify
or directly:
    python3 skills-daimon/tests/test_gamify.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Make `from gamify import ...` work when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))

import gamify  # noqa: E402
import history as history_mod  # noqa: E402
import render_report  # noqa: E402
from gamify import (  # noqa: E402
    BADGE_ORDER, GAME_SCHEMA_VERSION, MAX_DELTA_PER_TRACK, TRACKS,
    build_game_state, numeric_game_history_snapshot, render_grove_svg,
)


# Reusable fixtures
SAMPLE_PAYLOAD = {
    "session_count": 196,
    "outcomes": {
        "coverage": {"labeled": 60, "total": 196},
        "by_facet": {"fully_achieved": 25, "mostly_achieved": 23},
        "friction_sessions": {"wrong_approach": 21},
        "friction_counts_sum": {"wrong_approach": 25},
    },
    "tool_errors": {"Bash": {"ok": 2238, "error": 107}},
    "memory_events": {"sessions_with_memory": 11},
    "coaching_signals": {
        "native_tool_bypass": {
            "bypass_total": 395,
            "native_tool_use": {"Grep": 228, "Glob": 61, "Read": 1379},
        },
        "destructive_cmds": [
            {"label": "git push --force", "count": 6},
            {"label": "--no-verify (skips hooks)", "count": 4},
            {"label": "git reset --hard", "count": 4},
            {"label": "rm -rf", "count": 52},  # excluded
        ],
        "hot_repos_without_claudemd": [
            {"path": "/Users/u/Code/repo-a", "sessions": 4},
            {"path": "/Users/u/Code/repo-b", "sessions": 3},
        ],
    },
    "recurring_prompts": [{"prompt": "x" * 30, "count": 14}, {"prompt": "y" * 30, "count": 11}],
    "work_recap": {
        "top_projects": [
            {"path": "/x/repo-a", "kind": "dev", "sessions": 18},
            {"path": "/x/repo-b", "kind": "dev", "sessions": 14},
            {"path": "/x/Dragon Lodge", "kind": "writing", "sessions": 78},
        ],
    },
    "installed_skills": ["prompt-to-command"],
    "stuck_loops": [],
}


def _history(today="2026-05-25"):
    """Five days of prior numeric history. Latest entry is "yesterday"."""
    return [
        {"date": "2026-05-15", "window_days": 28,
         "scorecard": {"outcome_finished_pct": 72, "risky_git_count": 8,
                        "search_shell_pct": 21, "bash_error_pct": 5.4,
                        "memory_rate_pct": 4, "claudemd_missing": 4, "unsaved_prompts": 14},
         "game": {f"xp_{t}": 0 for t in TRACKS}},
        {"date": "2026-05-18", "window_days": 28,
         "scorecard": {"outcome_finished_pct": 75, "risky_git_count": 10,
                        "search_shell_pct": 20, "bash_error_pct": 5.1,
                        "memory_rate_pct": 4, "claudemd_missing": 3, "unsaved_prompts": 14},
         "game": {f"xp_{t}": 0 for t in TRACKS}},
        {"date": "2026-05-21", "window_days": 28,
         "scorecard": {"outcome_finished_pct": 77, "risky_git_count": 12,
                        "search_shell_pct": 19, "bash_error_pct": 4.9,
                        "memory_rate_pct": 5, "claudemd_missing": 3, "unsaved_prompts": 14},
         "game": {f"xp_{t}": 0 for t in TRACKS}},
        {"date": "2026-05-23", "window_days": 28,
         "scorecard": {"outcome_finished_pct": 79, "risky_git_count": 13,
                        "search_shell_pct": 18, "bash_error_pct": 4.7,
                        "memory_rate_pct": 5, "claudemd_missing": 2, "unsaved_prompts": 14},
         "game": {f"xp_{t}": 0 for t in TRACKS}},
    ]


PRIVACY_BLOCKLIST = (
    "/Users/u/", "/Users/", "Dragon Lodge", "repo-a", "repo-b",
    "session_id", "Authorization", "sk-", "git reset --hard",
    "Quest Secret", "Badge Secret",
)


def _gather_strings(obj):
    """Flatten every string leaf in a structure."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(_gather_strings(v))
        return out
    if isinstance(obj, (list, tuple)):
        out = []
        for v in obj:
            out.extend(_gather_strings(v))
        return out
    return []


def _render_with_history(payload, entries=None):
    old = history_mod.read_last
    history_mod.read_last = lambda *args, **kwargs: list(entries or [])
    try:
        return render_report.render(payload)
    finally:
        history_mod.read_last = old


class TestGamifyDeterminism(unittest.TestCase):
    def test_same_inputs_same_output(self):
        a = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25", window_days=28)
        b = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25", window_days=28)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))
        self.assertEqual(render_grove_svg(a["grove"]), render_grove_svg(b["grove"]))

    def test_schema_version_present(self):
        s = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        self.assertEqual(s["game_schema_version"], GAME_SCHEMA_VERSION)


class TestPrivacy(unittest.TestCase):
    def test_no_raw_user_strings_in_game_state(self):
        s = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        for blob in _gather_strings(s):
            for needle in PRIVACY_BLOCKLIST:
                self.assertNotIn(needle, blob, f"leaked {needle!r} via {blob!r}")

    def test_numeric_snapshot_only_numbers(self):
        s = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        snap = numeric_game_history_snapshot(s)
        for k, v in snap.items():
            self.assertIsInstance(v, (int, float),
                                   f"non-numeric key {k}: {v!r}")


class TestNoNegativeXp(unittest.TestCase):
    def test_xp_never_drops_when_signals_get_worse(self):
        # prior had 50 XP in safety; this run is worse — should NOT subtract.
        prior = [{"date": "2026-05-23", "window_days": 28,
                  "scorecard": {"risky_git_count": 1},
                  "game": {f"xp_{t}": (50 if t == "safety" else 0) for t in TRACKS}}]
        worse = dict(SAMPLE_PAYLOAD)  # safety worse (risky_git from 1 → many)
        s = build_game_state(worse, prior, today="2026-05-25")
        self.assertGreaterEqual(s["tracks"]["safety"]["xp"], 50)
        self.assertEqual(s["tracks"]["safety"]["delta"], 0)

    def test_delta_is_clamped_to_max(self):
        # An absurd improvement should still cap delta at MAX_DELTA_PER_TRACK.
        prior = [{"date": "2026-05-23", "window_days": 28,
                  "scorecard": {"risky_git_count": 999,
                                 "search_shell_pct": 99, "bash_error_pct": 99.9,
                                 "memory_rate_pct": 0, "outcome_finished_pct": 0,
                                 "claudemd_missing": 999, "unsaved_prompts": 999},
                  "game": {f"xp_{t}": 0 for t in TRACKS}}]
        s = build_game_state(SAMPLE_PAYLOAD, prior, today="2026-05-25")
        for t in TRACKS:
            self.assertLessEqual(s["tracks"][t]["delta"], MAX_DELTA_PER_TRACK)


class TestSameDayRerunDoesNotDuplicate(unittest.TestCase):
    def test_same_day_rerun_uses_prior_distinct(self):
        # If "today" already exists in history, the prior comparison should
        # skip it and use the most recent DISTINCT date.
        full = _history()
        full.append({
            "date": "2026-05-25", "window_days": 28,
            "scorecard": {"risky_git_count": 4, "search_shell_pct": 17,
                           "bash_error_pct": 4.6, "memory_rate_pct": 5,
                           "outcome_finished_pct": 80, "claudemd_missing": 2,
                           "unsaved_prompts": 14},
            "game": {f"xp_{t}": 100 if t == "safety" else 0 for t in TRACKS},
        })
        a = build_game_state(SAMPLE_PAYLOAD, full, today="2026-05-25")
        # The prior used should be 2026-05-23 (different date), so the safety
        # XP base is whatever 2026-05-23 had (0 in the fixture).
        # (Same logic ensures no double-award on a same-day rerun.)
        self.assertNotEqual(a["tracks"]["safety"]["xp"], 100)


class TestQuestSelection(unittest.TestCase):
    def test_risky_git_picks_safety_quest(self):
        s = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        self.assertIsNotNone(s["active_quest"])
        # Safety is highest priority and the fixture has risky_git=14
        self.assertEqual(s["active_quest"]["track"], "safety")
        self.assertEqual(s["active_quest"]["id"], "clear_git_thorns")

    def test_no_evidence_no_quest(self):
        clean = {
            "session_count": 50,
            "outcomes": {"coverage": {"labeled": 50, "total": 50},
                          "by_facet": {"fully_achieved": 50},
                          "friction_sessions": {},
                          "friction_counts_sum": {}},
            "tool_errors": {"Bash": {"ok": 100, "error": 0}},
            "memory_events": {"sessions_with_memory": 30, "verified_saved_learnings": 3},
            "coaching_signals": {
                "native_tool_bypass": {"bypass_total": 10,
                                        "native_tool_use": {"Read": 500}},
                "destructive_cmds": [],
                "hot_repos_without_claudemd": [],
            },
            "recurring_prompts": [],
            "work_recap": {"top_projects": []},
            "installed_skills": ["prompt-to-command"],
            "stuck_loops": [],
        }
        s = build_game_state(clean, None, today="2026-05-25")
        self.assertIsNone(s["active_quest"])

    def test_priority_order_when_safety_absent(self):
        # No safety evidence → automation wins (priority 2 > project_hygiene 3).
        p = json.loads(json.dumps(SAMPLE_PAYLOAD))
        p["coaching_signals"]["destructive_cmds"] = []
        s = build_game_state(p, _history(), today="2026-05-25")
        self.assertEqual(s["active_quest"]["id"], "plant_command_tree")

    def test_signpost_when_only_hygiene_evidence(self):
        # Only project_hygiene has evidence → signpost wins.
        p = json.loads(json.dumps(SAMPLE_PAYLOAD))
        p["coaching_signals"]["destructive_cmds"] = []         # no safety
        p["recurring_prompts"] = []                              # no automation
        p["outcomes"]["friction_sessions"] = {}                 # no planning
        p["outcomes"]["friction_counts_sum"] = {}
        p["memory_events"]["sessions_with_memory"] = 60         # memory rate high → no quest
        p["coaching_signals"]["native_tool_bypass"]["bypass_total"] = 0
        p["tool_errors"]["Bash"] = {"ok": 100, "error": 1}      # low bash err
        s = build_game_state(p, _history(), today="2026-05-25")
        self.assertEqual(s["active_quest"]["id"], "place_repo_signpost")


class TestHistorySnapshotShape(unittest.TestCase):
    def test_only_allowed_numeric_keys(self):
        s = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        snap = numeric_game_history_snapshot(s)
        allowed_prefixes = (
            "game_schema_version", "xp_total", "daimon_level", "badge_count",
            "badges_mask", "quests_offered_count", "quests_completed_count",
            "xp_", "level_", "delta_", "grove_", "constellation", "rhythm_",
        )
        for k in snap:
            self.assertTrue(
                any(k.startswith(p) or k == p for p in allowed_prefixes),
                f"unexpected snapshot key: {k}",
            )
        # Every value must be numeric.
        for k, v in snap.items():
            self.assertIsInstance(v, (int, float), f"{k} is not numeric")


class TestGroveSvg(unittest.TestCase):
    def test_self_contained_inline_svg(self):
        s = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        svg = render_grove_svg(s["grove"])
        # No external assets.
        self.assertNotIn("<image", svg)
        self.assertNotIn("xlink:href", svg)
        self.assertNotIn("script", svg.lower())
        # Accessibility plumbing.
        self.assertIn("<title>Daimon Grove</title>", svg)
        self.assertIn("<desc>", svg)
        self.assertIn('aria-label=', svg)
        # Six labeled glyphs.
        for label in ("Command Tree", "Memory Well", "Git Thorns",
                      "Planning Path", "Tool Shrine", "Repo Signpost"):
            self.assertIn(label, svg)


class TestBadgesAndConstellation(unittest.TestCase):
    def test_constellation_locks_below_three_distinct_days(self):
        s = build_game_state(SAMPLE_PAYLOAD, _history()[:2], today="2026-05-25")
        self.assertFalse(s["grove"]["constellation_unlocked"])
        s2 = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        self.assertTrue(s2["grove"]["constellation_unlocked"])

    def test_safe_hands_requires_three_clean_runs(self):
        # Fixture has risky_git=8/10/12/13 — not clean. Safe Hands is unearned.
        s = build_game_state(SAMPLE_PAYLOAD, _history(), today="2026-05-25")
        earned = {b["id"]: b["earned"] for b in s["badges"]}
        self.assertFalse(earned["safe_hands"])


class TestConservativeBadges(unittest.TestCase):
    def _clean_payload(self):
        return {
            "session_count": 30,
            "outcomes": {
                "coverage": {"labeled": 20, "total": 30},
                "by_facet": {"fully_achieved": 15, "mostly_achieved": 3},
                "friction_sessions": {},
                "friction_counts_sum": {},
            },
            "tool_errors": {"Bash": {"ok": 100, "error": 0}},
            "memory_events": {"sessions_with_memory": 12, "verified_saved_learnings": 3},
            "coaching_signals": {
                "native_tool_bypass": {"bypass_total": 1, "native_tool_use": {"Read": 20}},
                "destructive_cmds": [],
                "hot_repos_without_claudemd": [],
            },
            "recurring_prompts": [],
            "work_recap": {"top_projects": [{"path": "/x/repo", "kind": "dev", "sessions": 6}]},
            "installed_skills": ["prompt-to-command"],
            "stuck_loops": [],
        }

    def test_repo_warden_requires_active_repo_total(self):
        p = self._clean_payload()
        p["work_recap"]["top_projects"] = []
        s = build_game_state(p, None, today="2026-05-25")
        earned = {b["id"]: b["earned"] for b in s["badges"]}
        self.assertFalse(earned["repo_warden"])

    def test_pathfinder_requires_three_distinct_decreasing_days(self):
        p = self._clean_payload()
        p["outcomes"]["friction_sessions"] = {"wrong_approach": 2}
        p["outcomes"]["friction_counts_sum"] = {"wrong_approach": 2}
        hist = [
            {"date": "2026-05-21", "window_days": 28, "labeled": 20,
             "scorecard": {"wrong_approach_pct": 30, "risky_git_count": 0},
             "game": {f"xp_{t}": 0 for t in TRACKS}},
            {"date": "2026-05-23", "window_days": 28, "labeled": 20,
             "scorecard": {"wrong_approach_pct": 20, "risky_git_count": 0},
             "game": {f"xp_{t}": 0 for t in TRACKS}},
        ]
        s = build_game_state(p, hist, today="2026-05-25")
        earned = {b["id"]: b["earned"] for b in s["badges"]}
        self.assertTrue(earned["pathfinder"])

        s2 = build_game_state(p, hist[:1], today="2026-05-25")
        earned2 = {b["id"]: b["earned"] for b in s2["badges"]}
        self.assertFalse(earned2["pathfinder"])

    def test_pathfinder_blocks_not_enough_labeled_sessions(self):
        p = self._clean_payload()
        p["outcomes"]["coverage"] = {"labeled": 0, "total": 30}
        p["outcomes"]["friction_sessions"] = {}
        p["outcomes"]["friction_counts_sum"] = {}
        hist = [
            {"date": "2026-05-21", "window_days": 28, "labeled": 20,
             "scorecard": {"wrong_approach_pct": 30}, "game": {}},
            {"date": "2026-05-23", "window_days": 28, "labeled": 20,
             "scorecard": {"wrong_approach_pct": 20}, "game": {}},
        ]
        s = build_game_state(p, hist, today="2026-05-25")
        earned = {b["id"]: b["earned"] for b in s["badges"]}
        self.assertFalse(earned["pathfinder"])

    def test_safe_hands_requires_three_zero_windows(self):
        p = self._clean_payload()
        one_prior = [{"date": "2026-05-23", "window_days": 28,
                      "scorecard": {"risky_git_count": 0}, "game": {}}]
        s = build_game_state(p, one_prior, today="2026-05-25")
        earned = {b["id"]: b["earned"] for b in s["badges"]}
        self.assertFalse(earned["safe_hands"])

        two_prior = one_prior + [{"date": "2026-05-24", "window_days": 28,
                                  "scorecard": {"risky_git_count": 0}, "game": {}}]
        s2 = build_game_state(p, two_prior, today="2026-05-25")
        earned2 = {b["id"]: b["earned"] for b in s2["badges"]}
        self.assertTrue(earned2["safe_hands"])

    def test_memory_keeper_requires_three_saved_learnings(self):
        p = self._clean_payload()
        p["memory_events"]["verified_saved_learnings"] = 2
        s = build_game_state(p, None, today="2026-05-25")
        earned = {b["id"]: b["earned"] for b in s["badges"]}
        self.assertFalse(earned["memory_keeper"])


class TestQuestXpSeparation(unittest.TestCase):
    def test_offering_quest_does_not_award_xp(self):
        p = {
            "session_count": 20,
            "outcomes": {"coverage": {"labeled": 20, "total": 20},
                          "by_facet": {"fully_achieved": 20},
                          "friction_sessions": {}, "friction_counts_sum": {}},
            "tool_errors": {"Bash": {"ok": 100, "error": 0}},
            "memory_events": {"sessions_with_memory": 10, "verified_saved_learnings": 3},
            "coaching_signals": {"native_tool_bypass": {"bypass_total": 0, "native_tool_use": {"Read": 20}},
                                  "destructive_cmds": [], "hot_repos_without_claudemd": []},
            "recurring_prompts": [{"prompt": "secret raw prompt", "count": 8}],
            "work_recap": {"top_projects": [{"path": "/x/repo", "kind": "dev", "sessions": 3}]},
            "installed_skills": [],
            "stuck_loops": [],
        }
        hist = [{"date": "2026-05-23", "window_days": 28,
                 "scorecard": {"unsaved_prompts": 8}, "game": {f"xp_{t}": 0 for t in TRACKS}}]
        s = build_game_state(p, hist, today="2026-05-25")
        self.assertEqual(s["active_quest"]["id"], "plant_command_tree")
        self.assertEqual(s["tracks"]["automation"]["delta"], 0)
        self.assertEqual(s["tracks"]["automation"]["xp"], 0)

    def test_xp_awarded_after_verified_improvement(self):
        p = json.loads(json.dumps(SAMPLE_PAYLOAD))
        p["coaching_signals"]["destructive_cmds"] = []
        p["coaching_signals"]["hot_repos_without_claudemd"] = []
        p["recurring_prompts"] = [{"prompt": "secret raw prompt", "count": 2}]
        p["command_events"] = {"saved_from_repeated_prompt": 1}
        p["memory_events"]["verified_saved_learnings"] = 3
        hist = [{"date": "2026-05-23", "window_days": 28,
                 "scorecard": {"unsaved_prompts": 8},
                 "game": {f"xp_{t}": 0 for t in TRACKS}}]
        s = build_game_state(p, hist, today="2026-05-25")
        self.assertEqual(s["tracks"]["automation"]["delta"], 35)
        self.assertIn("verified as saved into a command", s["tracks"]["automation"]["evidence"])


class TestDisplayPolish(unittest.TestCase):
    def test_no_meaningless_zero_denominator_in_html(self):
        payload = {
            "meta": {"days": 28, "date": "2026-05-25"},
            "session_count": 0,
            "outcomes": {"coverage": {"labeled": 0, "total": 0},
                          "by_facet": {}, "friction_sessions": {}, "friction_counts_sum": {}},
            "tool_errors": {"Bash": {"ok": 0, "error": 0}},
            "memory_events": {"sessions_with_memory": 0},
            "coaching_signals": {"native_tool_bypass": {"bypass_total": 0, "native_tool_use": {}},
                                  "destructive_cmds": [], "hot_repos_without_claudemd": []},
            "recurring_prompts": [],
            "work_recap": {"top_projects": []},
            "installed_skills": [],
            "stuck_loops": [],
            "scorecard": [{
                "label": "Memory",
                "value": "0% of 0 sessions",
                "verdict": "no_data",
                "note": "Memory used in 0% of 0 sessions.",
                "explain": "0 of 0 sessions had memory activity.",
            }],
        }
        html = _render_with_history(payload, [])
        self.assertNotIn("0% of 0", html)
        self.assertNotIn("0 of 0 sessions", html)
        self.assertIn("Not enough session data to evaluate this area.", html)

    def test_grove_display_uses_human_labels_and_clear_delta(self):
        payload = json.loads(json.dumps(SAMPLE_PAYLOAD))
        payload["meta"] = {"days": 28, "date": "2026-05-25"}
        payload["command_events"] = {"saved_from_repeated_prompt": 1}
        html = _render_with_history(payload, _history())
        self.assertIn("Daimon Grove · Level", html)
        self.assertIn("RPG habit map", html)
        self.assertIn("Daimon Grove Adventure Map", html)
        self.assertIn("Why XP changed (evidence ledger)", html)
        self.assertIn("Landmark", html)
        self.assertIn("Relic", html)
        # Single strong label per mission card.
        self.assertRegex(html, r'class="quest-tag">(Active mission|Mission board)<')
        self.assertIn("XP verified this run", html)
        self.assertIn("Tool Fluency", html)
        self.assertIn("Project Hygiene", html)
        self.assertNotIn("tool_fluency", html)
        self.assertNotIn("project_hygiene", html)
        self.assertNotIn('<span class="delta-flat">·</span>', html)
        self.assertTrue("Active mission" in html or "No mission this run" in html)


class TestRedactionBoundaries(unittest.TestCase):
    def test_render_redacted_masks_html_output(self):
        payload = {
            "meta": {"days": 28, "date": "2026-05-25"},
            "verdict": {"name": "Snapshot",
                         "summary": "token sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                         "evidence_chips": [], "next_phrase": ""},
        }
        old = history_mod.read_last
        history_mod.read_last = lambda *args, **kwargs: []
        try:
            html = render_report.render_redacted(payload)
        finally:
            history_mod.read_last = old
        self.assertNotIn("sk-proj-AAAAAAAAAAAAAAAAAAAAAAAAAAAA", html)
        self.assertIn("sk-&lt;REDACTED&gt;", html)

    def test_history_game_drops_non_numeric_strings(self):
        scrubbed = history_mod._scrub_snapshot({
            "date": "2026-05-25",
            "window_days": 28,
            "sessions": 1,
            "labeled": 1,
            "scorecard": {"safe": 1, "evidence": "git reset --hard"},
            "game": {"xp_total": 10, "quest_title": "Quest Secret", "badge_name": "Badge Secret"},
        })
        self.assertEqual(scrubbed["game"], {"xp_total": 10})
        self.assertNotIn("evidence", scrubbed["scorecard"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
