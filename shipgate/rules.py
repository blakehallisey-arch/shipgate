"""What counts as a ship, which checks the diff demands, and the text that says so.

The deny message is the product. An agent that reads "blocked by shipgate" learns
nothing and either gives up or tries to route around it. An agent that reads
"design is required because public/index.html matches **/*.html; run
`shipgate run design`" does the right thing on the first try. So every unmet check
carries three things: the literal command that satisfies it, the path that
triggered it, and the human reason it exists.
"""
from __future__ import annotations

import os
import re
import shutil

from . import globs, gitinfo, state as state_mod

# Read-only `gh` verbs that happen to contain a ship word. Never gate these.
SAFE = re.compile(r"\bgh\s+pr\s+(list|view|checks|diff|status|create|comment)\b"
                  r"|\bgit\s+(log|status|diff|show|fetch|remote)\b", re.I)


def invocation():
    """How to spell `shipgate` on this machine, so the deny text is copy-pasteable."""
    return "shipgate" if shutil.which("shipgate") else "python3 -m shipgate"


# ---------------------------------------------------------------- ship detection

def split_segments(command):
    """One shell line into the commands it actually runs.

    `git status && git push origin main` is a ship. `echo "git push"` is not, but
    splitting is the cheap 90% and the SAFE list above covers the rest. shipgate
    is not a shell parser and does not pretend to be one — see "What this is not".
    """
    parts = re.split(r"&&|\|\||[;\n|]", command)
    return [p.strip() for p in parts if p.strip()]


def tokens(segment):
    out = []
    for tok in segment.split():
        if "=" in tok and re.match(r"^[A-Za-z_][A-Za-z_0-9]*=", tok) and not out:
            continue                              # leading FOO=bar env assignment
        out.append(tok)
    return out


def _contains_sequence(toks, phrase):
    want = phrase.split()
    if not want:
        return False
    for i in range(len(toks) - len(want) + 1):
        if [t.lower() for t in toks[i:i + len(want)]] == [w.lower() for w in want]:
            return True
    return False


def _push_targets_default(toks, default_branch, branch):
    """Is this `git push` aimed at the default branch?

    This is the line between "I am iterating on a branch" and "this is live".
    `git push origin feature-x` is not a ship and must never be gated, or the
    gate fires twenty times a day and gets uninstalled. `git push origin main` is.
    """
    args = toks[toks.index("push") + 1:] if "push" in toks else []
    if any(a in ("--dry-run", "-n", "--delete", "-d") for a in args):
        return False
    if any(a in ("--all", "--mirror") for a in args):
        return True
    positional = [a for a in args if not a.startswith("-")]
    # `-u origin main` — the flag eats no value, but `--repo x` style does not
    # appear in git push, so plain positional order is enough here.
    refspecs = positional[1:] if len(positional) > 1 else []
    if not refspecs:
        # No refspec: git pushes the current branch (or, on push.default=matching,
        # more than that). Current branch is the honest read.
        return bool(branch) and branch == default_branch
    for spec in refspecs:
        dest = spec.split(":")[-1]
        dest = dest.replace("refs/heads/", "")
        if dest == default_branch:
            return True
        if dest in ("HEAD", "") and branch == default_branch:
            return True
    return False


def is_ship(command, config, root=None, branch=None):
    """(bool, which configured phrase matched). Never raises."""
    if not command or SAFE.search(command):
        return False, ""
    if branch is None and root:
        branch = gitinfo.current_branch(root)
    for segment in split_segments(command):
        toks = tokens(segment)
        if not toks:
            continue
        for phrase in config.ship_commands:
            if not _contains_sequence(toks, phrase):
                continue
            low = phrase.lower()
            if low == "git push":
                if not _push_targets_default(toks, config.default_branch, branch or ""):
                    continue
            elif low == "git merge":
                # Merging INTO the default branch is the ship. Merging main down
                # into your feature branch is the opposite of a ship.
                if branch and branch != config.default_branch:
                    continue
            return True, phrase
    return False, ""


def leading_cd(command, cwd):
    """Follow a single leading `cd <dir> &&`, because that decides WHICH repo ships.

    The payload's cwd is the session's, not the command's, and in a tree of nested
    repos that difference makes the gate read the wrong diff. Fenced two ways: only
    one cd (a `cd a && cd .. && push` would leave us reading `a`), and only a real
    directory.
    """
    m = re.match(r"\s*cd\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))\s*&&", command or "")
    if not m or re.search(r"&&\s*cd\s", command):
        return cwd
    target = os.path.expanduser(next(g for g in m.groups() if g))
    if not os.path.isabs(target):
        target = os.path.join(cwd, target)
    return target if os.path.isdir(target) else cwd


# ------------------------------------------------------------------- evaluation

class Requirement:
    def __init__(self, check, trigger, status, pass_record=None):
        self.check = check
        self.trigger = trigger            # why the diff summoned it, in words
        self.status = status              # ok | missing | stale | expired
        self.pass_record = pass_record or {}

    @property
    def name(self):
        return self.check.name

    @property
    def met(self):
        return self.status == "ok"

    def command(self):
        base = invocation()
        if self.check.manual:
            return '%s pass %s --note "what you checked"' % (base, self.name)
        return "%s run %s" % (base, self.name)

    def as_dict(self):
        return {"name": self.name, "status": self.status, "trigger": self.trigger,
                "why": self.check.why, "how": self.check.how,
                "manual": self.check.manual, "command": self.command(),
                "passed_at": self.pass_record.get("at")}


def triggered_by(check, change):
    """Why this check applies to this diff, in a sentence — or "" if it does not."""
    if check.always:
        return "this check is marked always"
    if check.paths:
        hit = globs.first_match(change["files"], check.paths)
        if hit:
            return '%s matches "%s"' % (hit[0], hit[1])
    if check.lines_changed is not None and change["lines_changed"] >= check.lines_changed:
        return "%d lines changed (threshold %d)" % (change["lines_changed"],
                                                    check.lines_changed)
    return ""


def evaluate(config, change, state, tree):
    """Every check the diff summons, with its current status.

    A pass fails for three separate reasons, and the deny text names which one,
    because "run it again" and "you never ran it" are different messages:

      missing  never recorded for this ship
      stale    recorded, but the tree changed afterwards — this is the big one
      expired  recorded on this tree, but older than ttl_minutes
    """
    reqs = []
    passes = state.get("passes") or {}
    for check in config.checks:
        trigger = triggered_by(check, change)
        if not trigger:
            continue
        record = passes.get(check.name)
        if not record:
            status = "missing"
        elif record.get("tree") != tree:
            status = "stale"
        elif config.ttl_minutes:
            age = state_mod.age_minutes(record.get("at") or "")
            status = "expired" if (age is None or age > config.ttl_minutes) else "ok"
        else:
            status = "ok"
        reqs.append(Requirement(check, trigger, status, record))
    return reqs


# ------------------------------------------------------------------- the message

_STATUS_LINE = {
    "missing": "has not run for this change",
    "stale": "passed %s, but files changed after that, so the pass is stale",
    "expired": "passed %s, which is past the %d-minute window",
}


def summarize_files(files, limit=6):
    shown = ", ".join(files[:limit])
    if len(files) > limit:
        shown += " (+%d more)" % (len(files) - limit)
    return shown or "nothing shipgate could see"


def deny_text(config, change, reqs, phrase):
    unmet = [r for r in reqs if not r.met]
    met = [r for r in reqs if r.met]
    lines = []
    lines.append("shipgate: not yet. %d check%s this change needs %s not passed."
                 % (len(unmet), "" if len(unmet) == 1 else "s",
                    "has" if len(unmet) == 1 else "have"))
    lines.append("")
    lines.append("  Ship command: %s" % phrase)
    lines.append("  What it carries: %d file%s, %d lines changed"
                 % (len(change["files"]), "" if len(change["files"]) == 1 else "s",
                    change["lines_changed"]))
    lines.append("    %s" % summarize_files(change["files"]))
    lines.append("")
    for req in unmet:
        detail = _STATUS_LINE[req.status]
        if req.status == "stale":
            detail = detail % state_mod.human_age(req.pass_record.get("at") or "")
        elif req.status == "expired":
            detail = detail % (state_mod.human_age(req.pass_record.get("at") or ""),
                               config.ttl_minutes)
        lines.append("  [ ] %s — %s" % (req.name, detail))
        lines.append("      required because: %s" % req.trigger)
        if req.check.why:
            lines.append("      why it exists: %s" % req.check.why)
        if req.check.manual:
            lines.append("      no script can judge this one. Do it, then record it:")
        lines.append("      run: %s" % req.command())
        lines.append("")
    if met:
        lines.append("  Already satisfied: %s"
                     % ", ".join("%s (%s)" % (r.name,
                                              state_mod.human_age(r.pass_record.get("at") or ""))
                                 for r in met))
        lines.append("")
    lines.append("Run those, then run the ship command again.")
    lines.append("Passes are stamped with the current tree. Edit a file afterwards "
                 "and the check comes back — that is deliberate, a pass on an older "
                 "tree is not a pass on what you are about to ship.")
    lines.append("If a check genuinely does not apply here, say which one and why "
                 "before you go around this.")
    return "\n".join(lines)
