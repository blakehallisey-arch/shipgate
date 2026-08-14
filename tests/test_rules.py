"""Which commands are a ship, and which checks a given diff demands."""
import datetime as dt
import unittest

from util import SAMPLE_CONFIG, PROJECT  # noqa: F401

from shipgate.config import Config
from shipgate.rules import evaluate, is_ship, triggered_by

def _now():
    return dt.datetime.now().replace(microsecond=0).isoformat()


CFG = Config("/nowhere", SAMPLE_CONFIG)


def change(files, lines=10):
    return {"files": files, "lines_changed": lines, "base": "abc123"}


class TestShipDetection(unittest.TestCase):

    def test_push_to_the_default_branch_is_a_ship(self):
        self.assertTrue(is_ship("git push origin main", CFG, branch="main")[0])

    def test_push_to_a_feature_branch_is_not(self):
        self.assertFalse(is_ship("git push origin feature-branch", CFG,
                                 branch="feature-branch")[0])
        self.assertFalse(is_ship("git push -u origin feature-branch", CFG,
                                 branch="feature-branch")[0])

    def test_bare_push_follows_the_current_branch(self):
        self.assertTrue(is_ship("git push", CFG, branch="main")[0])
        self.assertFalse(is_ship("git push", CFG, branch="wip")[0])

    def test_head_colon_main_is_a_ship(self):
        self.assertTrue(is_ship("git push origin HEAD:main", CFG, branch="wip")[0])
        self.assertTrue(is_ship("git push origin HEAD:refs/heads/main", CFG,
                                branch="wip")[0])

    def test_dry_run_and_delete_are_not_ships(self):
        self.assertFalse(is_ship("git push --dry-run origin main", CFG,
                                 branch="main")[0])
        self.assertFalse(is_ship("git push origin --delete main", CFG,
                                 branch="main")[0])

    def test_pr_merge_is_always_a_ship_and_read_only_gh_is_not(self):
        self.assertTrue(is_ship("gh pr merge 12 --squash", CFG, branch="wip")[0])
        self.assertFalse(is_ship("gh pr view 12", CFG, branch="wip")[0])
        self.assertFalse(is_ship("gh pr diff 12", CFG, branch="wip")[0])

    def test_merge_counts_only_when_it_lands_on_the_default_branch(self):
        self.assertTrue(is_ship("git merge feature", CFG, branch="main")[0])
        self.assertFalse(is_ship("git merge main", CFG, branch="feature")[0])

    def test_a_ship_hidden_behind_a_chained_command_still_counts(self):
        ok, phrase = is_ship("git add -A && git commit -m x && git push origin main",
                             CFG, branch="main")
        self.assertTrue(ok)
        self.assertEqual(phrase, "git push")

    def test_ordinary_commands_are_untouched(self):
        for cmd in ["ls -la", "npm run build", "python3 -m pytest",
                    "git status", "git commit -m 'git push'", "git fetch origin"]:
            self.assertFalse(is_ship(cmd, CFG, branch="main")[0], cmd)


class TestTriggers(unittest.TestCase):

    def test_docs_only_change_requires_nothing(self):
        self.assertEqual(evaluate(CFG, change(["README.md", "docs/guide.md"]),
                                  {"passes": {}}, "tree:1"), [])

    def test_an_html_change_requires_design(self):
        reqs = evaluate(CFG, change(["public/index.html"]), {"passes": {}}, "tree:1")
        self.assertEqual([r.name for r in reqs], ["design"])
        self.assertIn("**/*.html", reqs[0].trigger)
        self.assertEqual(reqs[0].status, "missing")

    def test_a_big_change_requires_review_on_line_count_alone(self):
        reqs = evaluate(CFG, change(["notes.md"], lines=450), {"passes": {}}, "tree:1")
        self.assertEqual([r.name for r in reqs], ["review"])
        self.assertIn("450 lines changed", reqs[0].trigger)

    def test_just_under_the_line_threshold_does_not_trigger(self):
        check = [c for c in CFG.checks if c.name == "review"][0]
        self.assertEqual(triggered_by(check, change(["a.md"], lines=199)), "")
        self.assertNotEqual(triggered_by(check, change(["a.md"], lines=200)), "")

    def test_a_pass_on_this_tree_satisfies_the_check(self):
        # NOT a fixed timestamp. The check has a TTL, so a literal date here
        # passes on the morning it is written and fails on every clock after
        # that — which is exactly the "green check on nothing" failure this
        # tool exists to prevent, aimed at itself.
        st = {"passes": {"design": {"tree": "tree:1", "at": _now()}}}
        reqs = evaluate(CFG, change(["public/index.html"]), st, "tree:1")
        self.assertEqual(reqs[0].status, "ok")

    def test_a_pass_taken_on_a_different_tree_is_stale(self):
        st = {"passes": {"design": {"tree": "tree:OLD", "at": _now()}}}
        reqs = evaluate(CFG, change(["public/index.html"]), st, "tree:NEW")
        self.assertEqual(reqs[0].status, "stale")

    def test_a_pass_older_than_the_ttl_expires(self):
        st = {"passes": {"design": {"tree": "tree:1", "at": "2001-01-01T00:00:00"}}}
        reqs = evaluate(CFG, change(["public/index.html"]), st, "tree:1")
        self.assertEqual(reqs[0].status, "expired")

    def test_ttl_of_zero_means_no_expiry(self):
        cfg = Config("/nowhere", dict(SAMPLE_CONFIG, ttl_minutes=0))
        st = {"passes": {"design": {"tree": "tree:1", "at": "2001-01-01T00:00:00"}}}
        reqs = evaluate(cfg, change(["public/index.html"]), st, "tree:1")
        self.assertEqual(reqs[0].status, "ok")


if __name__ == "__main__":
    unittest.main()
