"""The path matcher. `**` is the whole reason this is not fnmatch."""
import unittest

from util import PROJECT  # noqa: F401  (puts the package on sys.path)

from shipgate.globs import first_match, matches


class TestGlobs(unittest.TestCase):

    def test_star_star_slash_spans_zero_directories(self):
        self.assertTrue(matches("index.html", "**/*.html"))
        self.assertTrue(matches("public/index.html", "**/*.html"))
        self.assertTrue(matches("a/b/c/index.html", "**/*.html"))

    def test_single_star_does_not_cross_a_slash(self):
        self.assertTrue(matches("src/app.py", "src/*.py"))
        self.assertFalse(matches("src/deep/app.py", "src/*.py"))

    def test_star_star_does_cross_a_slash(self):
        self.assertTrue(matches("src/deep/app.py", "src/**/*.py"))
        self.assertTrue(matches("src/app.py", "src/**/*.py"))
        self.assertTrue(matches("public/img/logo.png", "public/**"))

    def test_bare_pattern_matches_a_basename_at_any_depth(self):
        self.assertTrue(matches("a/b/style.css", "*.css"))

    def test_extension_is_not_a_prefix_match(self):
        self.assertFalse(matches("notes.html.bak", "**/*.html"))
        self.assertFalse(matches("src/app.pyc", "src/**/*.py"))

    def test_character_class_and_negation(self):
        self.assertTrue(matches("v1.css", "v[0-9].css"))
        self.assertFalse(matches("vx.css", "v[0-9].css"))
        self.assertFalse(matches("v1.css", "v[!0-9].css"))

    def test_question_mark_is_one_non_slash_character(self):
        self.assertTrue(matches("ab.css", "a?.css"))
        self.assertFalse(matches("a/b.css", "a?.css"))

    def test_leading_dot_slash_is_normalized(self):
        self.assertTrue(matches("./public/a.html", "**/*.html"))

    def test_first_match_reports_the_pair_the_deny_text_quotes(self):
        hit = first_match(["README.md", "public/index.html"], ["**/*.css", "**/*.html"])
        self.assertEqual(hit, ("public/index.html", "**/*.html"))
        self.assertIsNone(first_match(["README.md"], ["**/*.html"]))


if __name__ == "__main__":
    unittest.main()
