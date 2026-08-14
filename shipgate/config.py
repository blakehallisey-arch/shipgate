"""`.shipgate.json` — the rules, and the defaults when a key is missing.

JSON and not TOML because tomllib landed in 3.11 and this has to run on 3.9
with nothing installed.

A malformed config does not silently become an empty config. Empty means "no
checks required", which is the one wrong answer a gate can give. So a parse
error is carried as `config.error` and every caller reports it loudly; the hook
still allows the ship (see hook.py for why) but says, in the transcript, that it
did so blind.
"""
from __future__ import annotations

import json
import os

CONFIG_NAME = ".shipgate.json"

DEFAULT_SHIP_COMMANDS = [
    "git push",
    "gh pr merge",
    "git merge",
    "npm publish",
    "vercel deploy",
    "vercel --prod",
]

DEFAULTS = {
    "checks": [],
    "ship_commands": DEFAULT_SHIP_COMMANDS,
    "default_branch": "main",
    "ttl_minutes": 90,
}


class Check:
    """One rule: when it applies, how to satisfy it, and why it exists."""

    def __init__(self, raw):
        self.name = str(raw.get("name") or "").strip()
        when = raw.get("when") or {}
        self.paths = [str(p) for p in (when.get("paths") or [])]
        lines = when.get("lines_changed")
        self.lines_changed = int(lines) if isinstance(lines, (int, float)) else None
        self.always = bool(when.get("always"))
        how = raw.get("how")
        self.how = str(how) if how else None
        self.why = str(raw.get("why") or "").strip()
        self.satisfied_by = str(raw.get("satisfied_by") or
                                ("manual" if self.how is None else "command"))

    @property
    def manual(self):
        return self.how is None

    def as_dict(self):
        return {"name": self.name, "how": self.how, "why": self.why,
                "satisfied_by": self.satisfied_by,
                "when": {"paths": self.paths, "lines_changed": self.lines_changed,
                         "always": self.always}}


class Config:
    def __init__(self, root, raw=None, error=""):
        self.root = root
        self.error = error
        data = dict(DEFAULTS)
        data.update(raw or {})
        self.raw = data
        self.checks = [Check(c) for c in (data.get("checks") or []) if c.get("name")]
        self.ship_commands = [str(c) for c in (data.get("ship_commands") or [])]
        self.default_branch = str(data.get("default_branch") or "main")
        ttl = data.get("ttl_minutes")
        self.ttl_minutes = int(ttl) if isinstance(ttl, (int, float)) and ttl > 0 else 0

    @property
    def exists(self):
        return bool(self.raw.get("checks")) or os.path.exists(self.path)

    @property
    def path(self):
        return os.path.join(self.root or ".", CONFIG_NAME)

    def check(self, name):
        for c in self.checks:
            if c.name == name:
                return c
        return None


def load(root):
    """Read `.shipgate.json` from the repo root. Missing file is not an error."""
    path = os.path.join(root or ".", CONFIG_NAME)
    if not os.path.exists(path):
        return Config(root)
    try:
        with open(path) as fh:
            raw = json.load(fh)
    except Exception as exc:
        return Config(root, error="%s is not readable JSON: %s" % (CONFIG_NAME, exc))
    if not isinstance(raw, dict):
        return Config(root, error="%s must be a JSON object" % CONFIG_NAME)
    return Config(root, raw)
