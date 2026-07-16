"""Tests for the fast-path additions: skills.sh text parsing and batch search."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from catalog_search import (_installs_to_int, _trim_with_diversity,
                            dedupe_and_filter, parse_skills_sh_text, search_batch)

SAMPLE = """
Install with npx skills add <owner/repo@skill>

\x1b[38;5;145mxixu-me/skills@github-actions-docs\x1b[0m \x1b[36m208K installs\x1b[0m
\x1b[38;5;102m└ https://skills.sh/xixu-me/skills/github-actions-docs\x1b[0m

github/awesome-copilot@git-commit 34.9K installs
└ https://skills.sh/github/awesome-copilot/git-commit

shipshitdev/library@git-safety 114 installs
└ https://skills.sh/shipshitdev/library/git-safety

random prose line that must not match
"""


class TestSkillsShTextParse(unittest.TestCase):
    def test_parses_only_registry_shaped_lines(self):
        hits = parse_skills_sh_text(SAMPLE, "git")
        self.assertEqual([h["name"] for h in hits],
                         ["github-actions-docs", "git-commit", "git-safety"])

    def test_installs_suffixes(self):
        hits = parse_skills_sh_text(SAMPLE, "git")
        self.assertEqual([h["installs"] for h in hits], [208000, 34900, 114])
        self.assertEqual(_installs_to_int("1.2M"), 1200000)
        self.assertEqual(_installs_to_int("3,400"), 3400)

    def test_url_and_install_come_verbatim(self):
        hit = parse_skills_sh_text(SAMPLE, "git")[2]
        self.assertEqual(hit["source_url"], "https://skills.sh/shipshitdev/library/git-safety")
        self.assertEqual(hit["install"], ["npx skills add shipshitdev/library@git-safety"])

    def test_empty_text_yields_nothing(self):
        self.assertEqual(parse_skills_sh_text("", "git"), [])


def _hit(name, ctype, terms, installs=None):
    return {"name": name, "catalog_type": ctype, "matched_terms": terms,
            "installs": installs}


class TestRanking(unittest.TestCase):
    def test_install_boost_beats_incidental_multiword_match(self):
        # cockroachdb matches 2 generic words; git-safety matches 1 but has
        # 114 installs (+1 boost) -> tie on score, installs break the tie.
        hits = [_hit("cockroachdb", "marketplace", ["commit", "push"]),
                _hit("git-safety", "cli-registry", ["safety"], installs=114)]
        ranked = dedupe_and_filter(hits, set(), set())
        self.assertEqual(ranked[0]["name"], "git-safety")

    def test_thousand_installs_boost(self):
        # popular: 1 term + 2 boost = 3, ties three-words' 3 terms;
        # installs break the tie in popular's favor.
        hits = [_hit("three-words", "marketplace", ["a1x", "b2x", "c3x"]),
                _hit("popular", "cli-registry", ["a1x"], installs=2000)]
        ranked = dedupe_and_filter(hits, set(), set())
        self.assertEqual(ranked[0]["name"], "popular")

    def test_trim_keeps_registry_presence(self):
        cands = ([_hit(f"mp{i}", "marketplace", ["x", "y"]) for i in range(5)]
                 + [_hit("reg1", "cli-registry", ["x"], installs=40),
                    _hit("reg2", "cli-registry", ["x"], installs=30),
                    _hit("reg3", "cli-registry", ["x"], installs=20)])
        trimmed = _trim_with_diversity(cands, 5)
        names = [c["name"] for c in trimmed]
        self.assertEqual(len(trimmed), 5)
        self.assertIn("reg1", names)
        self.assertIn("reg2", names)
        self.assertNotIn("reg3", names)

    def test_trim_no_swap_when_registry_already_present(self):
        cands = [_hit("reg1", "cli-registry", ["x"], installs=99),
                 _hit("mp1", "marketplace", ["x"])]
        self.assertEqual(_trim_with_diversity(cands, 2), cands)


class TestSearchBatch(unittest.TestCase):
    def test_job_terms_keep_phrases_whole(self):
        from catalog_search import _job_terms
        self.assertEqual(_job_terms("git safety, commit push"),
                         ["git safety", "commit push"])
        self.assertEqual(_job_terms("web research"), ["web research"])

    def test_batch_trims_and_groups(self):
        # No catalogs -> empty candidates per job, but structure holds.
        scan = {"available_catalogs": [], "installed_skills": [], "ignored_names": []}
        out = search_batch(scan, ["git safety", "sql data"], top=3)
        self.assertEqual([j["job"] for j in out["jobs"]], ["git safety", "sql data"])
        self.assertTrue(all(j["candidates"] == [] for j in out["jobs"]))
        self.assertEqual(out["needs_live_probe"], [])


if __name__ == "__main__":
    unittest.main()
