"""Scratch git repos for the tests. Everything lives under a temp dir."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

ENV = dict(os.environ)
ENV.update({
    "GIT_AUTHOR_NAME": "shipgate tests", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "shipgate tests", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
})

SAMPLE_CONFIG = {
    "checks": [
        {"name": "design",
         "when": {"paths": ["**/*.html", "**/*.css", "**/*.svg", "public/**"]},
         "how": None, "why": "this ships something a human looks at",
         "satisfied_by": "manual"},
        {"name": "tests", "when": {"paths": ["src/**/*.py"]},
         "how": "python3 -c \"pass\""},
        {"name": "review", "when": {"lines_changed": 200}, "how": None,
         "why": "over 200 lines is not a tweak", "satisfied_by": "manual"},
    ],
    "ship_commands": ["git push", "gh pr merge", "git merge", "npm publish"],
    "default_branch": "main",
    "ttl_minutes": 90,
}


def git(root, *args):
    return subprocess.run(["git"] + list(args), cwd=root, env=ENV,
                          capture_output=True, text=True)


class Repo:
    """A throwaway repo on `main` with one commit and a .shipgate.json."""

    def __init__(self, config=None):
        self.path = os.path.realpath(tempfile.mkdtemp(prefix="shipgate-test-"))
        git(self.path, "init", "-q", "-b", "main")
        self.write(".gitignore", ".shipgate/\n")
        self.write("README.md", "start\n")
        if config is not False:
            self.write_json(".shipgate.json", config or SAMPLE_CONFIG)
        self.commit("first")

    def write(self, rel, text):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)
        return full

    def write_json(self, rel, data):
        return self.write(rel, json.dumps(data, indent=2) + "\n")

    def commit(self, message):
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", message)

    def branch(self, name):
        git(self.path, "checkout", "-q", "-b", name)

    def checkout(self, name):
        git(self.path, "checkout", "-q", name)

    def destroy(self):
        shutil.rmtree(self.path, ignore_errors=True)


def payload(command, cwd, event="PreToolUse", tool="Bash", response=None):
    data = {"hook_event_name": event, "tool_name": tool,
            "tool_input": {"command": command}, "cwd": cwd}
    if response is not None:
        data["tool_response"] = response
    return json.dumps(data)


def run_hook(text, cwd):
    """Run the hook exactly as Claude Code would: JSON on stdin, JSON on stdout."""
    proc = subprocess.run([sys.executable, "-m", "shipgate.hook"], input=text,
                          capture_output=True, text=True, cwd=cwd,
                          env=dict(ENV, PYTHONPATH=PROJECT))
    try:
        decision = json.loads(proc.stdout or "{}")
    except ValueError:
        decision = {"__unparseable__": proc.stdout}
    return proc, decision


def decision_of(obj):
    return ((obj.get("hookSpecificOutput") or {}).get("permissionDecision") or "allow")


def reason_of(obj):
    return (obj.get("hookSpecificOutput") or {}).get("permissionDecisionReason") or ""


def cli(args, cwd):
    return subprocess.run([sys.executable, "-m", "shipgate"] + args,
                          capture_output=True, text=True, cwd=cwd,
                          env=dict(ENV, PYTHONPATH=PROJECT))
