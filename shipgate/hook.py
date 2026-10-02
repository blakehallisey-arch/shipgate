#!/usr/bin/env python3
"""PreToolUse hook — stands in front of the commands that actually ship.

WHAT THIS CLOSES. A run of image work merged to the default branch six times in
one afternoon. The review step ran on the first merge and none of the five after
it. The design review never ran at all, on work whose entire output was pictures.
The result was a stock airport photo illustrating an API story on the live site,
found by the person who owned the checklist that said to run the reviews.

The checklist was not wrong. It lost because every one of those six merges felt
like another step in the same iteration rather than a ship, so "when I'm done"
became the trigger and that moment never arrived. A note loses to a fast loop.
A hook does not.

HOW IT MUST NOT CRY WOLF. A gate that fires on every ship is a gate people turn
off, and an uninstalled gate catches nothing. So the required checks come from
the DIFF, not from the topic and not from a fixed list. A docs-only merge sails
through in silence. A merge that carries a page a human looks at asks for the
design review, by name, with the command.

WHICH WAY IT FAILS. Claude Code reads an empty stdout as "allow". The quiet
fall-throughs (not a repo, no config, no diff to read, a payload that is not
JSON) still allow, and each one writes a line to `.shipgate/hook.log` saying so.

A crash INSIDE the hook is different, and it used to allow too. The theory was
that a broken hook blocking every push gets ripped out within the hour. The
trade was wrong: an exception on the one command the gate exists for meant the
ship went through unchecked, and the only trace was a line in a log nobody
opens. So a crash on a SHIP command now denies, puts the error in the
transcript, and points at the log. A crash on anything else still allows, so a
dead hook cannot block `ls`. A merge that waits a minute while someone reads a
traceback is the cheap outcome. A page shipping past a dead gate is the
expensive one.

Reads the tool call on stdin as JSON, writes a decision on stdout.
"""
from __future__ import annotations

import json
import os
import sys
import traceback

if __package__ in (None, ""):
    # Claude Code invokes this by path — `python3 .../shipgate/hook.py` — which
    # gives relative imports nothing to be relative to. Put the checkout on the
    # path and carry on, so the settings entry can stay one readable line.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "shipgate"
    import shipgate  # noqa: F401

from . import config as config_mod
from . import gitinfo, rules
from . import state as state_mod

SHIP_TOOLS = ("Bash",)

# Set the moment run() learns the command is a ship. If the hook crashes after
# that point there is no question left about which way to fail. POST_EVENT marks
# a PostToolUse call, where the command has already run and a deny would be a
# PreToolUse-shaped answer to a question nobody asked. SHIP_PHRASES is the
# repo's own configured list, kept for the crudest fallback.
SHIPPING = None
POST_EVENT = False
SHIP_PHRASES = None


def emit(obj):
    print(json.dumps(obj))
    sys.exit(0)


def allow():
    emit({})


def deny(reason):
    emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}})


def log(root, message):
    """Say which way it failed. Rule: a guard that fails must not fail quietly."""
    if not root:
        return
    try:
        os.makedirs(state_mod.state_dir(root), exist_ok=True)
        with open(os.path.join(state_mod.state_dir(root), "hook.log"), "a") as fh:
            fh.write("%s %s\n" % (state_mod.now_iso(), message))
    except Exception:
        pass


def landed(payload):
    """Did the command PostToolUse is reporting on actually succeed?

    Only an explicit failure signal counts as a failure. Anything ambiguous is
    read as landed, which spends the passes — stricter, never blinder.
    """
    response = payload.get("tool_response")
    if isinstance(response, dict):
        if response.get("success") is False:
            return False
        if response.get("is_error") is True:
            return False
        if response.get("interrupted") is True:
            return False
    return True


def run(payload):
    global SHIPPING, POST_EVENT, SHIP_PHRASES
    SHIPPING, POST_EVENT, SHIP_PHRASES = None, False, None
    if payload.get("tool_name") not in SHIP_TOOLS:
        allow()

    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command:
        allow()

    cwd = payload.get("cwd") or os.getcwd()
    cwd = rules.leading_cd(command, cwd)
    root = gitinfo.repo_root(cwd)
    if not root:
        allow()                       # not a git repo; nothing to read a diff from

    cfg = config_mod.load(root)
    SHIP_PHRASES = list(cfg.ship_commands or [])

    if payload.get("hook_event_name") == "PostToolUse":
        POST_EVENT = True
        if rules.is_ship(command, cfg, root)[0]:
            state_mod.settle_ship(root, landed=landed(payload))
        allow()

    shipping, phrase = rules.is_ship(command, cfg, root)
    if not shipping:
        allow()                       # the silent 99%: branch pushes, reads, builds
    SHIPPING = phrase

    if cfg.error:
        log(root, "ALLOWED BLIND: %s" % cfg.error)
        allow()
    if not cfg.checks:
        allow()                       # configured with no rules is a real answer

    change = gitinfo.changed(root, cfg.default_branch)
    if not change["files"]:
        # Nothing readable. Common and legitimate: re-pushing an already-pushed
        # branch, or a deploy command run after the merge landed. Allowing here
        # is the cry-wolf trade — blocking on an empty read is the fastest way to
        # make someone uninstall this.
        log(root, "allowed: ship command %r but no diff visible" % phrase)
        allow()

    tree = gitinfo.tree_sha(root)
    st = state_mod.load(root)
    reqs = rules.evaluate(cfg, change, st, tree)
    unmet = [r for r in reqs if not r.met]

    if unmet:
        deny(rules.deny_text(cfg, change, reqs, phrase))

    # Every check this ship needed has passed. Park the passes and let it through;
    # the next change starts from nothing. Per ship, not per session.
    state_mod.begin_ship(root, command=command)
    allow()


def looks_like_ship(command, root):
    """Was the command that crashed the hook a ship?

    Known for certain once run() got past its is_ship call. Before that point
    it is re-derived, and if even that raises, the raw text is read against the
    built-in ship phrases. Three tries, each cruder than the last, because the
    answer decides whether a crash blocks the command or lets it through."""
    if SHIPPING:
        return True
    try:
        return rules.is_ship(command, config_mod.load(root), root)[0]
    except Exception:
        low = command.lower()
        phrases = SHIP_PHRASES or config_mod.DEFAULT_SHIP_COMMANDS
        return any(p.lower() in low for p in phrases)


def crash_text(tb):
    last = tb.strip().splitlines()[-1] if tb.strip() else "unknown error"
    return ("shipgate crashed while checking this ship and will not let it "
            "through blind: %s. The full traceback is in .shipgate/hook.log. "
            "Fix the hook, or uninstall it on purpose and ship without it." % last)


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            allow()
    except SystemExit:
        raise
    except Exception:
        allow()                       # a malformed payload must never block a turn
    try:
        run(payload)
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        command = str((payload.get("tool_input") or {}).get("command") or "")
        try:
            root = gitinfo.repo_root(rules.leading_cd(command, payload.get("cwd") or os.getcwd()))
        except Exception:
            root = None
        if POST_EVENT:
            # The command already ran. There is nothing left to deny, and a
            # PreToolUse-shaped deny here would be noise after the fact.
            log(root, "allowed: PostToolUse, the command already ran; the hook crashed settling it:\n%s" % tb)
            allow()
        if looks_like_ship(command, root):
            log(root, "DENIED after an exception inside the hook:\n%s" % tb)
            deny(crash_text(tb))
        log(root, "allowed: not a ship command, and the hook crashed on it anyway:\n%s" % tb)
        allow()


if __name__ == "__main__":
    main()
