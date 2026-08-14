#!/usr/bin/env python3
"""`shipgate` — status, run, pass, reset, init.

The hook is the enforcement. This is the part a human uses: ask what would be
required right now, satisfy a check, or start over.

Exit codes follow the house rule. 0 fine, 1 error, 2 "stop and look up" — so
`shipgate status` exits 2 when something is unmet, which makes it usable as the
last line of a script without parsing anything.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from . import config as config_mod
from . import gitinfo, rules
from . import state as state_mod


def die(message, code=1):
    sys.stderr.write("shipgate: %s\n" % message)
    return code


def context(args):
    """(root, config) or (None, None) — everything below needs both."""
    root = gitinfo.repo_root(getattr(args, "repo", None) or os.getcwd())
    if not root:
        return None, None
    return root, config_mod.load(root)


def gather(root, cfg):
    change = gitinfo.changed(root, cfg.default_branch)
    tree = gitinfo.tree_sha(root)
    st = state_mod.load(root)
    return change, tree, st, rules.evaluate(cfg, change, st, tree)


# ------------------------------------------------------------------------ status

def cmd_status(args):
    root, cfg = context(args)
    if not root:
        return die("not inside a git repository")
    if cfg.error:
        if args.json:
            print(json.dumps({"error": cfg.error}, indent=2))
        return die(cfg.error)

    change, tree, st, reqs = gather(root, cfg)
    unmet = [r for r in reqs if not r.met]

    if args.json:
        print(json.dumps({
            "repo": root,
            "config": cfg.path if os.path.exists(cfg.path) else None,
            "default_branch": cfg.default_branch,
            "branch": gitinfo.current_branch(root),
            "compared_against": change["base"] or None,
            "tree": tree,
            "files": change["files"],
            "lines_changed": change["lines_changed"],
            "ttl_minutes": cfg.ttl_minutes,
            "required": [r.as_dict() for r in reqs],
            "ready_to_ship": not unmet,
        }, indent=2))
        return 0 if not unmet else 2

    if not os.path.exists(cfg.path):
        print("No %s in %s — nothing is gated." % (config_mod.CONFIG_NAME, root))
        print("Write one with: %s init" % rules.invocation())
        return 0

    basis = ("against %s" % change["base"][:12]) if change["base"] else \
        "uncommitted work only (no default branch or upstream to compare against)"
    print("repo    %s" % root)
    print("branch  %s (default: %s)" % (gitinfo.current_branch(root) or "detached",
                                        cfg.default_branch))
    print("change  %d files, %d lines, measured %s"
          % (len(change["files"]), change["lines_changed"], basis))
    print("        %s" % rules.summarize_files(change["files"], limit=8))
    print("tree    %s" % tree)
    print("")

    if not reqs:
        print("Nothing this change touches requires a check. A ship would go "
              "straight through.")
        return 0

    for req in reqs:
        mark = "x" if req.met else " "
        print("  [%s] %-12s %s" % (mark, req.name, req.trigger))
        if req.met:
            print("       passed %s%s"
                  % (state_mod.human_age(req.pass_record.get("at") or ""),
                     (" — %s" % req.pass_record["note"]) if req.pass_record.get("note") else ""))
        else:
            print("       %s — %s" % (req.status, req.command()))
    print("")
    if unmet:
        print("Not shippable: %d unmet." % len(unmet))
        return 2
    print("Shippable. Every check this change needs has passed on this tree.")
    return 0


# --------------------------------------------------------------------------- run

def cmd_run(args):
    root, cfg = context(args)
    if not root:
        return die("not inside a git repository")
    check = cfg.check(args.name)
    if not check:
        return die("no check named %r in %s" % (args.name, config_mod.CONFIG_NAME))
    if check.manual:
        return die("%r has no command — it is satisfied by hand:\n  %s pass %s "
                   '--note "what you checked"' % (args.name, rules.invocation(),
                                                  args.name))

    print("shipgate: running %s -> %s" % (check.name, check.how), flush=True)
    proc = subprocess.run(check.how, cwd=root, shell=True)
    if proc.returncode != 0:
        return die("%s failed (exit %d). Nothing recorded." % (check.name,
                                                               proc.returncode))
    tree = gitinfo.tree_sha(root)
    state_mod.record_pass(root, check.name, tree, note=args.note or "",
                          how=check.how, kind="command")
    print("shipgate: %s passed, recorded against %s" % (check.name, tree))
    return 0


# -------------------------------------------------------------------------- pass

def cmd_pass(args):
    root, cfg = context(args)
    if not root:
        return die("not inside a git repository")
    check = cfg.check(args.name)
    if not check:
        return die("no check named %r in %s" % (args.name, config_mod.CONFIG_NAME))
    if not check.manual:
        # Allowed, but said out loud. shipgate records that you said so; it has
        # no way to know whether the thing actually happened.
        print("shipgate: note — %s has a command (%s). Recording your word for it."
              % (check.name, check.how))
    tree = gitinfo.tree_sha(root)
    state_mod.record_pass(root, check.name, tree, note=args.note or "",
                          how=check.how, kind="manual")
    print("shipgate: %s recorded against %s%s"
          % (check.name, tree, (" — %s" % args.note) if args.note else ""))
    print("          Valid for this tree only. Change a file and it comes back.")
    return 0


# ------------------------------------------------------------------------- reset

def cmd_reset(args):
    root, _ = context(args)
    if not root:
        return die("not inside a git repository")
    state_mod.reset(root)
    print("shipgate: cleared every recorded pass in %s" % root)
    return 0


# -------------------------------------------------------------------------- init

def sniff(root):
    """Guess a starter config from what is actually in the repo."""
    checks = []

    def exists(*names):
        return any(os.path.exists(os.path.join(root, n)) for n in names)

    def find(extensions, skip=(".git", "node_modules", ".shipgate", "dist", "build")):
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for name in files:
                if name.lower().endswith(extensions):
                    return True
        return False

    if find((".html", ".css", ".svg")):
        checks.append({
            "name": "design",
            "when": {"paths": ["**/*.html", "**/*.css", "**/*.svg", "public/**",
                               "**/*.png", "**/*.jpg", "**/*.jpeg", "**/*.webp"]},
            "how": None,
            "why": "this ships something a human looks at",
            "satisfied_by": "manual",
        })
    if exists("package.json"):
        checks.append({
            "name": "tests",
            "when": {"paths": ["src/**", "lib/**", "**/*.js", "**/*.ts",
                               "**/*.jsx", "**/*.tsx"]},
            "how": "npm test",
            "why": "code changed and the suite is cheap",
        })
    if exists("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"):
        checks.append({
            "name": "tests",
            "when": {"paths": ["**/*.py"]},
            "how": "python3 -m pytest -q",
            "why": "code changed and the suite is cheap",
        })
    checks.append({
        "name": "review",
        "when": {"lines_changed": 200},
        "how": None,
        "why": "over 200 lines is not a tweak",
        "satisfied_by": "manual",
    })

    seen, unique = set(), []
    for check in checks:                     # npm + python both named "tests"
        if check["name"] in seen:
            continue
        seen.add(check["name"])
        unique.append(check)

    branch = gitinfo.current_branch(root)
    default = branch if branch in ("main", "master") else "main"
    return {
        "checks": unique,
        "ship_commands": config_mod.DEFAULT_SHIP_COMMANDS,
        "default_branch": default,
        "ttl_minutes": 90,
    }


def ignore_state(root):
    """Keep `.shipgate/` out of the branch. Idempotent, appends, never rewrites."""
    path = os.path.join(root, ".gitignore")
    try:
        existing = open(path).read() if os.path.exists(path) else ""
    except Exception:
        return False
    if any(line.strip() in (".shipgate", ".shipgate/") for line in existing.splitlines()):
        return False
    with open(path, "a") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write("\n# shipgate records which checks passed, per machine, not per branch\n"
                 ".shipgate/\n")
    return True


def cmd_init(args):
    root, _ = context(args)
    if not root:
        return die("not inside a git repository")
    path = os.path.join(root, config_mod.CONFIG_NAME)
    if os.path.exists(path) and not args.force:
        return die("%s already exists. Use --force to overwrite." % path)
    data = sniff(root)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    print("shipgate: wrote %s" % path)
    if ignore_state(root):
        print("shipgate: added .shipgate/ to .gitignore")
    for check in data["checks"]:
        how = check["how"] or "by hand"
        print("  %-10s %s" % (check["name"], how))
    print("")
    print("Read it and cut what does not apply. A check that fires on every ship "
          "is a check people learn to ignore.")
    return 0


# -------------------------------------------------------------------------- main

def build_parser():
    parser = argparse.ArgumentParser(
        prog="shipgate",
        description="Will not let a ship through until the checks this change "
                    "actually needs have run.")
    parser.add_argument("--repo", help="path inside the repo (default: cwd)")
    subs = parser.add_subparsers(dest="cmd")

    p = subs.add_parser("status", help="what would be required to ship right now")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    p.set_defaults(func=cmd_status)

    p = subs.add_parser("run", help="run a check's command and record the pass")
    p.add_argument("name")
    p.add_argument("--note", help="anything worth remembering about this run")
    p.set_defaults(func=cmd_run)

    p = subs.add_parser("pass", help="record a check you satisfied by hand")
    p.add_argument("name")
    p.add_argument("--note", help="what you actually checked")
    p.set_defaults(func=cmd_pass)

    p = subs.add_parser("reset", help="clear every recorded pass")
    p.set_defaults(func=cmd_reset)

    p = subs.add_parser("init", help="write a starter .shipgate.json")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
