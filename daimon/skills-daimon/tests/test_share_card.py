"""Tests for the redesigned shareable archetype card."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

import render_report as rr

ARCH = {"title": "The Builder-Scribe", "tagline": "idea to artifact, fast"}
MIX = {"dev": 46, "writing": 42, "data": 7, "ops": 6}


class TestPillText(unittest.TestCase):
    def test_balanced_grove_headlines(self):
        self.assertEqual(rr.share_pill_text(5, 3, True), "🌌 Balanced Grove · Level 5")

    def test_level_and_badges(self):
        self.assertEqual(rr.share_pill_text(4, 3, False), "✨ Daimon Level 4 · 3 badges")

    def test_singular_badge(self):
        self.assertEqual(rr.share_pill_text(2, 1, False), "✨ Daimon Level 2 · 1 badge")

    def test_tolerant_of_junk(self):
        self.assertEqual(rr.share_pill_text(None, None, False), "✨ Daimon Level 0 · 0 badges")


class TestTopMix(unittest.TestCase):
    def test_top_two_sorted(self):
        self.assertEqual(rr._top_mix(MIX), "46% dev · 42% writing")

    def test_empty(self):
        self.assertEqual(rr._top_mix({}), "")


class TestIntroLabel(unittest.TestCase):
    def test_drops_article_when_title_has_one(self):
        self.assertEqual(rr._intro_label("The Builder-Scribe"), "You’re")
        self.assertEqual(rr._intro_label("A Tinkerer"), "You’re")
        self.assertEqual(rr._intro_label("An Architect"), "You’re")

    def test_keeps_article_otherwise(self):
        self.assertEqual(rr._intro_label("Builder-Scribe"), "You’re a")
        self.assertEqual(rr._intro_label("Theorist"), "You’re a")  # not "the"

    def test_no_double_article_in_card(self):
        svg = rr.build_share_card_svg({"title": "The Builder-Scribe", "tagline": "t"},
                                      MIX, 475, 28, 4, 3, False, size="link")
        self.assertNotIn("You’re a</text><text", svg)  # sanity: label rendered
        self.assertNotIn("a The", svg.replace("\n", " "))


class TestBuildCard(unittest.TestCase):
    def _card(self, size):
        return rr.build_share_card_svg(ARCH, MIX, 475, 28, 4, 3, False, size=size)

    def test_each_size_has_right_dimensions(self):
        for size, (w, h) in {"square": (1080, 1080), "story": (1080, 1920),
                             "link": (1200, 630)}.items():
            svg = self._card(size)
            self.assertIn(f'viewBox="0 0 {w} {h}"', svg)
            self.assertIn(f'width="{w}" height="{h}"', svg)

    def test_hero_shows_session_count(self):
        svg = self._card("square")
        self.assertIn("475", svg)
        self.assertIn("SESSIONS", svg)
        self.assertIn("in 28 days", svg)

    def test_identity_and_pill(self):
        svg = self._card("square")
        self.assertIn("The Builder-Scribe", svg)
        self.assertIn("idea to artifact, fast", svg)
        self.assertIn("Daimon Level 4 · 3 badges", svg)

    def test_balanced_pill_when_balanced(self):
        svg = rr.build_share_card_svg(ARCH, MIX, 475, 28, 5, 4, True, size="square")
        self.assertIn("Balanced Grove", svg)

    def test_unknown_size_falls_back_to_square(self):
        svg = rr.build_share_card_svg(ARCH, MIX, 10, 28, 1, 0, False, size="bogus")
        self.assertIn('viewBox="0 0 1080 1080"', svg)

    def test_well_formed_xml(self):
        import xml.dom.minidom as minidom
        for size in ("square", "story", "link"):
            minidom.parseString(self._card(size))  # raises if malformed

    def test_thousands_separator(self):
        svg = rr.build_share_card_svg(ARCH, MIX, 1475, 28, 4, 3, False, size="square")
        self.assertIn("1,475", svg)

    def test_no_identifying_data_leaks(self):
        # The card must never carry project paths, recs, or coaching text.
        svg = self._card("story")
        for bad in ("/Users/", "Dragon Lodge", "git-safety", "npx skills",
                    "reset --hard", "wp-calypso"):
            self.assertNotIn(bad, svg)

    def test_escapes_archetype(self):
        svg = rr.build_share_card_svg({"title": "A & B <x>", "tagline": "t"},
                                      MIX, 5, 28, 1, 0, False)
        self.assertNotIn("<x>", svg)
        self.assertIn("&amp;", svg)


class TestRenderIntegration(unittest.TestCase):
    def test_share_js_present_and_png(self):
        # The share path must rasterize to PNG, not ship SVG to social.
        self.assertIn("sdShareCard", rr.SHARE_JS)
        self.assertIn("image/png", rr.SHARE_JS)
        self.assertIn("navigator.share", rr.SHARE_JS)


if __name__ == "__main__":
    unittest.main()
