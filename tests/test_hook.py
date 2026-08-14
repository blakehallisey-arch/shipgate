"""The hook, end to end: real repos, real diffs, real stdin/stdout.

The deny paths are the point. A guard with no test on its denies is decoration.
"""
import json
import os
import unittest

import util
from util import Repo, cli, decision_of, payload, reason_of, run_hook


class HookCase(unittest.TestCase):

    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.destroy()

    def ship(self, command="git push origin main", **kw):
        return run_hook(payload(command, self.repo.path, **kw), self.repo.path)[1]


class TestSilence(HookCase):

    def test_a_non_ship_command_is_untouched(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        for cmd in ["ls", "npm run build", "git status", "git push origin feature-x"]:
            self.assertEqual(self.ship(cmd), {}, cmd)

    def test_a_non_bash_tool_is_untouched(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        out = run_hook(payload("git push origin main", self.repo.path, tool="Edit"),
                       self.repo.path)[1]
        self.assertEqual(out, {})

    def test_a_malformed_payload_does_not_block(self):
        for bad in ["", "not json at all", "[1,2,3]", "{\"tool_name\":", "null"]:
            proc, out = run_hook(bad, self.repo.path)
            self.assertEqual(proc.returncode, 0, bad)
            self.assertEqual(out, {}, bad)

    def test_the_hook_runs_by_path_the_way_settings_json_invokes_it(self):
        import subprocess
        import sys
        self.repo.write("public/index.html", "<p>new</p>\n")
        env = dict(util.ENV)
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, os.path.join(util.PROJECT, "shipgate", "hook.py")],
            input=payload("git push origin main", self.repo.path),
            capture_output=True, text=True, cwd=self.repo.path, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(decision_of(json.loads(proc.stdout)), "deny")

    def test_a_docs_only_change_ships_in_silence(self):
        self.repo.write("README.md", "start\nplus a docs line\n")
        self.repo.write("docs/guide.md", "how to\n")
        self.assertEqual(self.ship(), {})

    def test_a_repo_with_no_config_is_not_gated(self):
        plain = Repo(config=False)
        try:
            plain.write("public/index.html", "<p>x</p>\n")
            out = run_hook(payload("git push origin main", plain.path), plain.path)[1]
            self.assertEqual(out, {})
        finally:
            plain.destroy()


class TestDeny(HookCase):

    def test_an_html_change_is_denied_and_names_the_command_to_run(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        out = self.ship()
        self.assertEqual(decision_of(out), "deny")
        reason = reason_of(out)
        self.assertIn("design", reason)
        self.assertIn("public/index.html", reason)
        self.assertIn("**/*.html", reason)
        self.assertIn("pass design", reason)
        self.assertIn("this ships something a human looks at", reason)

    def test_gh_pr_merge_is_gated_from_a_feature_branch(self):
        self.repo.branch("art")
        self.repo.write("public/hero.css", "body{}\n")
        self.repo.commit("hero")
        out = self.ship("gh pr merge 4 --squash")
        self.assertEqual(decision_of(out), "deny")
        self.assertIn("public/hero.css", reason_of(out))

    def test_a_pass_lets_the_same_tree_through(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        self.assertEqual(decision_of(self.ship()), "deny")
        cli(["pass", "design", "--note", "looked at the rendered page"], self.repo.path)
        self.assertEqual(self.ship(), {})

    def test_a_pass_goes_stale_the_moment_a_file_changes(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        cli(["pass", "design", "--note", "looked at it"], self.repo.path)

        # One more edit after the review — the thing that actually happened in
        # real life, where the ship went out on the strength of the earlier read.
        self.repo.write("public/index.html", "<p>new</p><img src=airport.jpg>\n")
        out = self.ship()
        self.assertEqual(decision_of(out), "deny")
        self.assertIn("stale", reason_of(out))

    def test_the_ttl_expires_a_pass_on_an_unchanged_tree(self):
        self.repo.write_json(".shipgate.json", dict(util.SAMPLE_CONFIG, ttl_minutes=1))
        self.repo.write("public/index.html", "<p>new</p>\n")
        cli(["pass", "design"], self.repo.path)

        path = os.path.join(self.repo.path, ".shipgate", "state.json")
        with open(path) as fh:
            data = json.load(fh)
        data["passes"]["design"]["at"] = "2001-01-01T00:00:00"
        with open(path, "w") as fh:
            json.dump(data, fh)

        out = self.ship()
        self.assertEqual(decision_of(out), "deny")
        self.assertIn("window", reason_of(out))

    def test_a_line_count_alone_can_require_a_check(self):
        self.repo.write("notes.md", "\n".join("line %d" % i for i in range(400)))
        out = self.ship()
        self.assertEqual(decision_of(out), "deny")
        self.assertIn("review", reason_of(out))
        self.assertIn("threshold 200", reason_of(out))

    def test_a_runnable_check_names_run_not_pass(self):
        self.repo.write("src/app.py", "x = 1\n")
        reason = reason_of(self.ship())
        self.assertIn("run tests", reason)


class TestShipResets(HookCase):

    def test_state_resets_after_a_ship_so_the_next_change_starts_fresh(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        cli(["pass", "design"], self.repo.path)
        self.assertEqual(self.ship(), {})

        # PostToolUse settles the ship the way Claude Code would report it.
        run_hook(payload("git push origin main", self.repo.path,
                         event="PostToolUse", response={"success": True}),
                 self.repo.path)

        self.repo.write("public/about.html", "<p>page two</p>\n")
        out = self.ship()
        self.assertEqual(decision_of(out), "deny")

    def test_a_failed_ship_hands_the_passes_back(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        cli(["pass", "design"], self.repo.path)
        self.assertEqual(self.ship(), {})
        run_hook(payload("git push origin main", self.repo.path,
                         event="PostToolUse", response={"success": False}),
                 self.repo.path)
        # Nothing changed on disk, so the review it already had still counts.
        self.assertEqual(self.ship(), {})


class TestCli(HookCase):

    def test_status_exits_two_when_something_is_unmet(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        proc = cli(["status"], self.repo.path)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("design", proc.stdout)

    def test_status_json_says_whether_it_is_shippable(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        data = json.loads(cli(["status", "--json"], self.repo.path).stdout)
        self.assertFalse(data["ready_to_ship"])
        self.assertEqual([r["name"] for r in data["required"]], ["design"])
        cli(["pass", "design"], self.repo.path)
        data = json.loads(cli(["status", "--json"], self.repo.path).stdout)
        self.assertTrue(data["ready_to_ship"])

    def test_run_executes_the_command_and_records_the_pass(self):
        self.repo.write("src/app.py", "x = 1\n")
        proc = cli(["run", "tests"], self.repo.path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.ship(), {})

    def test_run_records_nothing_when_the_command_fails(self):
        self.repo.write_json(".shipgate.json", {
            "checks": [{"name": "tests", "when": {"paths": ["src/**/*.py"]},
                        "how": "python3 -c \"import sys; sys.exit(3)\""}],
            "default_branch": "main"})
        self.repo.write("src/app.py", "x = 1\n")
        proc = cli(["run", "tests"], self.repo.path)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(decision_of(self.ship()), "deny")

    def test_run_refuses_a_manual_check(self):
        proc = cli(["run", "design"], self.repo.path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("satisfied by hand", proc.stderr)

    def test_reset_clears_the_passes(self):
        self.repo.write("public/index.html", "<p>new</p>\n")
        cli(["pass", "design"], self.repo.path)
        self.assertEqual(self.ship(), {})
        cli(["reset"], self.repo.path)
        self.assertEqual(decision_of(self.ship()), "deny")

    def test_init_sniffs_the_repo(self):
        fresh = Repo(config=False)
        try:
            fresh.write("public/index.html", "<p>x</p>\n")
            fresh.write("pyproject.toml", "[project]\nname='x'\n")
            proc = cli(["init"], fresh.path)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(os.path.join(fresh.path, ".shipgate.json")) as fh:
                data = json.load(fh)
            names = [c["name"] for c in data["checks"]]
            self.assertIn("design", names)
            self.assertIn("tests", names)
            self.assertIn("review", names)
        finally:
            fresh.destroy()


if __name__ == "__main__":
    unittest.main()
