"""`.shipgate/state.json` — which checks have passed, and on which tree.

WHERE THE STATE LIVES: `<repo root>/.shipgate/state.json`, and nowhere else.
shipgate never writes outside the repo it is installed in. `install.sh` appends
`.shipgate/` to that repo's `.gitignore` — the passes are yours, not the
branch's, and a pass committed into the tree would also change the tree it was
recorded against, which is a loop.

THE PENDING DANCE. PreToolUse fires on the way IN. At that moment all shipgate
knows is that it is willing to let the command run — not that the push landed. A
merge that then fails on a conflict used to burn the review credit anyway, so the
next attempt demanded a review that had already happened and could never be
un-demanded. So an allowed ship moves the passes to `pending` instead of dropping
them, and PostToolUse either finalizes (drop them) or restores.

When PostToolUse cannot tell what happened, it FINALIZES. That direction spends
a pass that maybe should not have been spent, which leaves the gate stricter than
it needs to be. The other direction hands passes back for a ship that did land,
which lets the next one through unchecked. Between an annoying gate and a blind
one, take the annoying one.
"""
from __future__ import annotations

import datetime as dt
import json
import os

DIR_NAME = ".shipgate"
FILE_NAME = "state.json"
VERSION = 1


def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def state_dir(root):
    return os.path.join(root, DIR_NAME)


def state_path(root):
    return os.path.join(state_dir(root), FILE_NAME)


def blank():
    return {"version": VERSION, "passes": {}, "pending": None, "last_ship": None}


def load(root):
    try:
        with open(state_path(root)) as fh:
            data = json.load(fh)
    except Exception:
        return blank()
    if not isinstance(data, dict):
        return blank()
    out = blank()
    out.update(data)
    if not isinstance(out.get("passes"), dict):
        out["passes"] = {}
    return out


def save(root, data):
    os.makedirs(state_dir(root), exist_ok=True)
    tmp = state_path(root) + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, state_path(root))
    return data


def record_pass(root, name, tree, note="", how=None, kind="command"):
    data = load(root)
    data["passes"][name] = {"tree": tree, "at": now_iso(), "note": note or "",
                            "how": how, "kind": kind}
    return save(root, data)


def reset(root):
    data = load(root)
    data["passes"] = {}
    data["pending"] = None
    return save(root, data)


def begin_ship(root, command=""):
    """An allowed ship: park the passes, do not spend them yet."""
    data = load(root)
    if data.get("pending"):
        # A second ship arrived before the first was settled. The first one is
        # water under the bridge — settle it as landed and start clean.
        data["pending"] = None
    data["pending"] = {"passes": data.get("passes") or {}, "at": now_iso(),
                       "command": command}
    data["passes"] = {}
    data["last_ship"] = now_iso()
    return save(root, data)


def settle_ship(root, landed=True):
    """PostToolUse: keep the reset (landed) or put the passes back (it failed)."""
    data = load(root)
    pending = data.get("pending")
    if not pending:
        return data
    if not landed:
        restored = dict(pending.get("passes") or {})
        restored.update(data.get("passes") or {})     # anything passed since wins
        data["passes"] = restored
        data["last_ship"] = None
    data["pending"] = None
    return save(root, data)


def age_minutes(stamp):
    try:
        then = dt.datetime.fromisoformat(stamp)
    except Exception:
        return None
    return max(0.0, (dt.datetime.now() - then).total_seconds() / 60.0)


def human_age(stamp):
    minutes = age_minutes(stamp)
    if minutes is None:
        return "at an unknown time"
    if minutes < 1:
        return "less than a minute ago"
    if minutes < 60:
        return "%d minute%s ago" % (int(minutes), "" if int(minutes) == 1 else "s")
    hours = minutes / 60.0
    if hours < 24:
        return "%.1f hours ago" % hours
    return "%.1f days ago" % (hours / 24.0)
