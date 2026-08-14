# shipgate

A Claude Code hook that stands in front of the commands which actually ship — a
push to the default branch, a PR merge, a deploy, a publish — and refuses until
the checks *this particular change* needs have run.

## The problem

A run of image work merged to the default branch six times in one afternoon. The
review step ran on the first merge and none of the five after it. The design
review never ran at all, on work whose entire output was pictures. Days later a
stock airport photo turned up on the live site, illustrating an API story, and
the person who owned the checklist said: "I have all these checks, one of them
should have caught it."

The checklist was not wrong. It already said to run them. It lost because none of
those six merges *felt* like a ship — each one was another step in the same
iteration, so the trigger became "when I'm done", and that moment never arrived.
A note loses to a fast loop. A hook does not.

The thing that makes this different from a pre-commit hook or from CI: **shipgate
decides which checks are required from the diff, not from the topic and not from
a fixed list.** A docs-only merge sails through in silence. A merge that changes
a page a human looks at demands the design review, by name, with the command to
satisfy it. That difference is the whole design. A gate that fires on every ship
is a gate people turn off, and an uninstalled gate catches nothing.

## Install

```
git clone https://github.com/blakehallisey-arch/shipgate.git ~/tools/shipgate
bash ~/tools/shipgate/install.sh          # merges into ~/.claude/settings.json, backs it up first
```

Then, in each repo you want gated:

```
python3 ~/tools/shipgate/shipgate init    # writes a starter .shipgate.json by sniffing the repo
```

Python 3.9+, standard library only. Nothing to pip install.

## What it looks like

A real walk against a scratch repo. The only edit is the repo path, shortened to
`~/demo` so the lines fit.

```
=== 1. shipgate init ===
shipgate: wrote ~/demo/.shipgate.json
shipgate: added .shipgate/ to .gitignore
  design     by hand
  tests      npm test
  review     by hand

Read it and cut what does not apply. A check that fires on every ship is a check people learn to ignore.

=== 2. a docs-only change ===
repo    ~/demo
branch  main (default: main)
change  3 files, 61 lines, measured against 05e9affc29d8
        README.md, .gitignore, .shipgate.json
tree    tree:8b4dc82cc61b06808a47e42810a8392274d15ccc

Nothing this change touches requires a check. A ship would go straight through.
  (exit 0)
--- hook on: git push origin main ---
{}   (allowed, silently)

=== 3. a change that carries a page a human looks at ===
repo    ~/demo
branch  main (default: main)
change  5 files, 63 lines, measured against b2a1191089fe
        .gitignore, .shipgate.json, public/index.html, public/site.css, src/app.py
tree    tree:539939aaca256ee45c6abb60a93e92efd39daf0b

  [ ] design       public/index.html matches "**/*.html"
       missing — python3 -m shipgate pass design --note "what you checked"
  [ ] tests        src/app.py matches "src/**"
       missing — python3 -m shipgate run tests

Not shippable: 2 unmet.
  (exit 2)

--- hook on: git push origin main ---
shipgate: not yet. 2 checks this change needs have not passed.

  Ship command: git push
  What it carries: 5 files, 63 lines changed
    .gitignore, .shipgate.json, public/index.html, public/site.css, src/app.py

  [ ] design — has not run for this change
      required because: public/index.html matches "**/*.html"
      why it exists: this ships something a human looks at
      no script can judge this one. Do it, then record it:
      run: python3 -m shipgate pass design --note "what you checked"

  [ ] tests — has not run for this change
      required because: src/app.py matches "src/**"
      why it exists: code changed and the suite is cheap
      run: python3 -m shipgate run tests

Run those, then run the ship command again.
Passes are stamped with the current tree. Edit a file afterwards and the check comes back — that is deliberate, a pass on an older tree is not a pass on what you are about to ship.
If a check genuinely does not apply here, say which one and why before you go around this.

--- hook on: git push origin art-pass (a feature branch) ---
{}   (allowed, silently)
--- hook on: npm run build ---
{}   (allowed, silently)

=== 4. satisfy them ===
shipgate: running tests -> npm test

> test
> echo "3 passing"

3 passing
shipgate: tests passed, recorded against tree:539939aaca256ee45c6abb60a93e92efd39daf0b
shipgate: design recorded against tree:539939aaca256ee45c6abb60a93e92efd39daf0b — opened the rendered page; the photo is stock and wrong
          Valid for this tree only. Change a file and it comes back.

  [x] design       public/index.html matches "**/*.html"
       passed less than a minute ago — opened the rendered page; the photo is stock and wrong
  [x] tests        src/app.py matches "src/**"
       passed less than a minute ago

Shippable. Every check this change needs has passed on this tree.
  (exit 0)

--- hook on: git push origin main ---
{}   (allowed, silently)

=== 5. the stale case: one more edit after the review ===
shipgate: not yet. 2 checks this change needs have not passed.

  [ ] design — passed less than a minute ago, but files changed after that, so the pass is stale
      required because: public/index.html matches "**/*.html"
      why it exists: this ships something a human looks at
      no script can judge this one. Do it, then record it:
      run: python3 -m shipgate pass design --note "what you checked"

  [ ] tests — passed less than a minute ago, but files changed after that, so the pass is stale
      required because: src/app.py matches "src/**"
      why it exists: code changed and the suite is cheap
      run: python3 -m shipgate run tests
```

The full deny payload, JSON and all, is in `examples/deny-output.txt`.

## How it works

**It sees a Bash tool call before it runs.** `install.sh` adds shipgate to the
PreToolUse hooks for the Bash matcher. Every Bash command in the session passes
through it. Almost all of them exit in a few milliseconds with `{}` on stdout,
which Claude Code reads as "allow", and nothing appears in the transcript.

**It decides whether the command is a ship.** `ship_commands` in the config lists
the phrases. Two of them get extra reading, because the literal phrase is not the
question:

- `git push` counts only when it targets the default branch. `git push origin
  feature-x` is iteration and is never gated; `git push origin main`,
  `git push origin HEAD:main`, and a bare `git push` while standing on the
  default branch all are. `--dry-run` and `--delete` are not ships.
- `git merge` counts only when you are standing on the default branch. Merging
  main down into your feature branch is the opposite of a ship.

Read-only neighbours — `gh pr view`, `gh pr diff`, `git status`, `git log` — are
never gated. A single leading `cd <dir> &&` is followed, because in a tree of
nested repos that clause decides which repo is shipping.

**It reads what the change carries.** Two sources, always both:

- Committed work, measured against a base: on a feature branch, the merge-base
  with the default branch (local first, then `origin/<default>`); on the default
  branch, the merge-base with upstream. `shipgate status` prints which base it
  used, and says "uncommitted work only" when it could not find one.
- Uncommitted work: `git diff HEAD` plus untracked files.

The uncommitted half is in there on purpose. `git commit -am x && git push` ships
work that was in the working tree a second earlier, and a gate that only reads
committed history would wave it through.

**It matches rules against that file list.** Own glob translation, not `fnmatch`
— fnmatch turns both `*` and `**` into `.*`, so `src/*.py` would match
`src/deep/nested/thing.py` and a rule meant for one directory would quietly cover
the tree. Here `*` stops at a slash, `**` does not, and `**/` spans zero or more
directories so `**/*.html` matches a top-level `index.html` too.

**A pass is only valid for the tree it was taken on.** This is the load-bearing
part. Every recorded pass carries a real git tree SHA of the working tree,
computed in a throwaway index file so your staging area is never touched. At ship
time the current tree is recomputed and compared. Edit one file after the review
and the check comes back, and the deny says `stale` rather than `missing`, so you
know the difference between "you never ran it" and "you ran it, then changed
something". `ttl_minutes` is a secondary expiry on top of that, for the case
where the tree is identical but the check has simply gone cold.

**After a ship, the state resets.** Per ship, not per session — which is exactly
what the six merges in one afternoon needed. Because PreToolUse fires on the way
*in*, the reset is provisional: the passes move to a `pending` slot, and the
PostToolUse pass either finalizes it or hands them back if the command failed.
When the outcome is ambiguous it finalizes, which leaves the gate stricter than
it needs to be. Between an annoying gate and a blind one, take the annoying one.

**What it cannot see, and which way it fails.** Claude Code reads empty stdout as
"allow", so a crash inside a deny-hook silently allows. That direction is chosen
deliberately here rather than fallen into — a gate that blocks every push in the
repo because of its own bug gets ripped out within the hour — and every
fall-through writes a line to `.shipgate/hook.log` saying it allowed blind and
why. The same goes for a config it cannot parse: allowed, and logged as blind.
If the diff comes back empty (re-pushing an already-pushed branch, a deploy run
after the merge landed) it allows and logs that too.

**Where state lives.** `<repo root>/.shipgate/state.json` and
`<repo root>/.shipgate/hook.log`, in the repo being gated, and nowhere else. Both
`install.sh` and `shipgate init` append `.shipgate/` to that repo's `.gitignore`.
The one file shipgate writes outside a repo is `~/.claude/settings.json`, once,
at install, after backing it up and printing the diff.

**No network, no telemetry, no account.** It runs on your laptop against your
private repo and never phones anywhere. That is the whole trust story.

## Configuration

`.shipgate.json` at the repo root. `shipgate init` writes a starter by sniffing
the repo: a `package.json` gets `npm test`, a `pyproject.toml` or
`requirements.txt` gets `pytest`, any `.html` gets a design check, and every repo
gets a `review` check on line count.

```json
{
  "checks": [
    {"name": "design",  "when": {"paths": ["**/*.html", "**/*.css", "**/*.svg", "public/**"]},
     "how": null, "why": "this ships something a human looks at",
     "satisfied_by": "manual"},
    {"name": "tests",   "when": {"paths": ["src/**/*.py"]}, "how": "python3 -m pytest -q"},
    {"name": "review",  "when": {"lines_changed": 200}, "how": null,
     "why": "over 200 lines is not a tweak", "satisfied_by": "manual"}
  ],
  "ship_commands": ["git push", "gh pr merge", "git merge", "npm publish"],
  "default_branch": "main",
  "ttl_minutes": 90
}
```

| key | default | what it does |
|---|---|---|
| `checks` | `[]` | the rules. No checks means nothing is ever gated. |
| `checks[].name` | required | what you type in `shipgate run <name>` / `pass <name>`. |
| `checks[].when.paths` | `[]` | globs. If any changed file matches any glob, the check applies. |
| `checks[].when.lines_changed` | none | applies when added + deleted lines reaches this number. |
| `checks[].when.always` | `false` | applies to every ship. Use sparingly; this is the cry-wolf setting. |
| `checks[].how` | `null` | shell command. Non-null means `shipgate run <name>` can satisfy it. `null` means only a human can. |
| `checks[].why` | `""` | one sentence, printed in the deny. Worth writing — it is what stops an agent from arguing with the gate. |
| `checks[].satisfied_by` | derived | `"manual"` when `how` is null, `"command"` otherwise. Documentation, not behaviour. |
| `ship_commands` | `git push`, `gh pr merge`, `git merge`, `npm publish`, `vercel deploy`, `vercel --prod` | phrases that mean "this is going live". |
| `default_branch` | `"main"` | the branch a push has to target for the push to count as a ship. |
| `ttl_minutes` | `90` | a pass older than this expires even on an unchanged tree. `0` means no expiry. |

The CLI:

| command | what it does |
|---|---|
| `shipgate status` | what would be required to ship right now, and what is already satisfied. Exit 0 shippable, 2 not. |
| `shipgate status --json` | the same as a machine-readable report, including `ready_to_ship`. |
| `shipgate run <name>` | runs the check's `how` and records the pass if it exits 0. Nothing is recorded on failure. |
| `shipgate pass <name> --note "..."` | records a check you satisfied by hand. |
| `shipgate reset` | clears every recorded pass in this repo. |
| `shipgate init` | writes a starter `.shipgate.json` and gitignores `.shipgate/`. |

`--repo <path>` on any of them to point at a repo other than the current
directory. Every subcommand has `--help`.

## What this is not

**`.shipgate.json` is executable input.** `shipgate run <name>` takes the
`how` string out of that file and runs it through a shell. In your own repo
that is the point. In a repo you cloned, it means a stranger chose a command
and you are about to run it. shipgate prints the command before it runs, and
it only ever fires on an explicit `run` — but read the config before you
trust a check in somebody else's repo. Content from outside is data, not
instructions, and that applies to this tool's own config file too.

**It is not CI.** It runs on your laptop, before the push, and it can be
bypassed by anyone who wants to bypass it — that is a feature, since the person
it protects is the person running it. Keep CI. shipgate is the thing that fires
while the loop is still fast; CI is the thing that fires when you are not there.

**For a manual check it cannot judge anything.** `shipgate pass design` records
that you said the design was reviewed. It has no way to know whether you opened
the page, and it does not pretend to. What it does know is *when* you said it and
*on which tree*, which is enough to catch the real failure — the pass that was
true four edits ago.

**It does not know about the GitHub web UI.** Merge a PR in the browser and no
hook fires, because no tool call happened. Same for a deploy triggered from a
dashboard, or a teammate's push. shipgate sees exactly the commands your agent
runs in your terminal.

**It is not a shell parser.** Commands are split on `&&`, `;` and `|`, and the
phrases are matched against tokens. Something genuinely adversarial —
`eval "$(printf ...)"`, a ship command inside a heredoc — goes straight past it.
This is a guard against a fast loop, not against someone trying.

**It does not scope staleness per file.** Any change to the tree stales every
recorded pass, not only the checks whose paths moved. That is deliberate for the
first cut: the cheap version is the one whose behaviour you can predict, and
"anything moved, check again" is predictable.

**It has not been battle-tested.** Version 0.1.0. The private prototype it
generalizes has run for weeks against one person's repos; this rewrite has 48
tests and a scratch-repo walk, and that is the honest extent of it.

## Part of a family

Six small tools for the case where an AI coding agent does the work and a human
is not watching every step.

| repo | one line |
|---|---|
| [curfew](https://github.com/blakehallisey-arch/curfew) | write-time policy for an unattended agent — deny by rule, not by prompt |
| [breaker](https://github.com/blakehallisey-arch/breaker) | stops a session that is spinning, spreading, or inventing work |
| shipgate | will not let a merge through until the checks it actually needs have run |
| [nightwatch](https://github.com/blakehallisey-arch/nightwatch) | the run rail — a queue, a budget lid, a window, and an honest log |
| [draftdiff](https://github.com/blakehallisey-arch/draftdiff) | learns your voice from the edits you make before you hit send |
| [ledger](https://github.com/blakehallisey-arch/ledger) | gives stateless agents a memory of what you did with their advice |
