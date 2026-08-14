"""Everything shipgate asks git.

Two questions matter here.

WHAT IS SHIPPING. Not "what did I touch this session" — what this push or merge
would actually carry. On a feature branch that is the branch against the default
branch, plus whatever is still uncommitted. On the default branch itself there is
no branch to compare, so it is what is ahead of upstream, plus whatever is still
uncommitted. Both cases include the uncommitted half on purpose: `git push` after
`git commit -am` ships work that was only in the working tree a second ago.

WHAT TREE WAS A CHECK PASSED AGAINST. This is the load of the whole tool. A pass
recorded before you edited three more files is not a pass on what you are about
to ship. So every pass is stamped with a real git tree SHA of the working tree —
built in a throwaway index file so the user's own staging area is never touched —
and a pass whose stamp does not equal the tree at ship time is stale.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile

TIMEOUT = 15

# shipgate's own state is never part of the change and never part of the tree.
# Without this, recording a pass edits `.shipgate/state.json`, which changes the
# tree, which makes the pass that was just recorded stale — a loop where the gate
# can never be satisfied. `install.sh` and `shipgate init` also add `.shipgate/`
# to .gitignore, but a repo that skipped that step must not break.
EXCLUDE = [".", ":(exclude).shipgate"]


def run(args, cwd, env=None):
    """Run a git command. Returns stdout on success, empty string on anything else.

    Never raises. A gate that dies because git printed something unexpected is a
    gate that stops running.
    """
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                              timeout=TIMEOUT, env=env)
    except Exception:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def repo_root(cwd):
    """Absolute, symlink-resolved repo root. None when cwd is not in a repo."""
    top = run(["git", "rev-parse", "--show-toplevel"], cwd).strip()
    return os.path.realpath(top) if top else None


def current_branch(root):
    name = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root).strip()
    return name if name and name != "HEAD" else ""


def has_head(root):
    return bool(run(["git", "rev-parse", "--verify", "HEAD"], root).strip())


def ref_exists(root, ref):
    return bool(run(["git", "rev-parse", "--verify", "--quiet", ref], root).strip())


def upstream(root):
    return run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
               root).strip()


def diff_base(root, default_branch):
    """The commit this ship is measured against, or "" when there isn't one.

    Order matters. The local default branch first, then origin's copy, because a
    stale `origin/main` can be weeks behind and would make a two-file change look
    like it carries a whole release. Reading the diff wrong in the direction of
    "more" is how a gate ends up firing on everything.
    """
    if not has_head(root):
        return ""
    branch = current_branch(root)
    candidates = []
    if branch and branch != default_branch:
        candidates += [default_branch, "origin/" + default_branch]
    up = upstream(root)
    if up:
        candidates.append(up)
    if branch == default_branch:
        # On the default branch with no upstream configured, origin's copy is the
        # only honest answer to "what is not out there yet".
        candidates.append("origin/" + default_branch)
    for ref in candidates:
        if ref_exists(root, ref):
            base = run(["git", "merge-base", ref, "HEAD"], root).strip()
            if base:
                return base
    return ""


def _numstat(lines):
    files, changed = [], 0
    for line in lines.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted, path = parts[0], parts[1], parts[-1]
        # Renames arrive as `old => new` inside braces; the new name is what ships.
        if " => " in path:
            path = path.split(" => ")[-1].rstrip("}")
        files.append(path.strip())
        for value in (added, deleted):
            if value.isdigit():                 # "-" means binary; no line count
                changed += int(value)
    return files, changed


def _untracked(root):
    out = run(["git", "ls-files", "--others", "--exclude-standard", "--"] + EXCLUDE, root)
    files, changed = [], 0
    for path in out.splitlines():
        path = path.strip()
        if not path:
            continue
        files.append(path)
        full = os.path.join(root, path)
        try:
            with open(full, "rb") as fh:
                blob = fh.read(1024 * 1024)
            if b"\0" not in blob:               # binary files carry no line count
                changed += blob.count(b"\n") + (0 if blob.endswith(b"\n") else 1)
        except Exception:
            pass
    return files, changed


def changed(root, default_branch):
    """What this ship carries: {files, lines_changed, base}.

    `base` is echoed back so `shipgate status` can say out loud what it compared
    against. When it is empty the answer is uncommitted work only, and status
    says that too — a number with no stated basis is worse than no number.
    """
    files, lines = [], 0
    base = diff_base(root, default_branch)
    if base:
        got, count = _numstat(run(["git", "diff", "--numstat", base + "..HEAD", "--"] + EXCLUDE, root))
        files += got
        lines += count
    if has_head(root):
        got, count = _numstat(run(["git", "diff", "--numstat", "HEAD", "--"] + EXCLUDE, root))
        files += got
        lines += count
    got, count = _untracked(root)
    files += got
    lines += count

    seen, unique = set(), []
    for path in files:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return {"files": unique, "lines_changed": lines, "base": base}


def tree_sha(root):
    """A git tree SHA for the working tree, including untracked files.

    Built against a throwaway GIT_INDEX_FILE so the user's real staging area is
    never touched — shipgate must not be able to change what your next commit
    contains. .gitignore applies, and `.shipgate/` is excluded outright, so
    recording a pass cannot invalidate the pass it just recorded.

    Falls back to a content hash if git will not cooperate. The fallback is
    marked in the returned string so a stale-check across the two never silently
    compares apples to oranges.
    """
    fd, index = tempfile.mkstemp(prefix="shipgate-index-")
    os.close(fd)
    os.unlink(index)                            # git wants to create it itself
    try:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = index
        run(["git", "add", "-A", "--"] + EXCLUDE, root, env=env)
        sha = run(["git", "write-tree"], root, env=env).strip()
        if sha:
            return "tree:" + sha
    finally:
        try:
            os.unlink(index)
        except OSError:
            pass

    digest = hashlib.sha256()
    for part in (run(["git", "rev-parse", "HEAD"], root),
                 run(["git", "status", "--porcelain"], root),
                 run(["git", "diff", "HEAD"], root)):
        digest.update(part.encode("utf-8", "replace"))
    return "hash:" + digest.hexdigest()
