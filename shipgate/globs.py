"""Glob matching for path rules.

WHY THIS EXISTS AND NOT `fnmatch`. fnmatch flattens `*` and `**` to the same
thing: it translates both to `.*`, which matches across `/`. So `src/*.py`
would match `src/deep/nested/thing.py`, and a rule meant to cover one directory
would quietly cover the whole tree. A gate that over-matches fires on changes it
has no business firing on, and a gate that fires on everything gets turned off.

So the three forms are separated on purpose:

    *       anything except a slash
    **      anything, slashes included
    **/     zero or more directories (so `**/*.html` matches `index.html` too)

One convenience: a pattern with no slash in it also matches a bare filename at
any depth, so `*.css` behaves the way people expect it to.
"""
from __future__ import annotations

import re

_CACHE = {}


def translate(pattern: str) -> str:
    """Turn one glob into a regex source string."""
    out = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 3] == "**/":
                # zero-or-more directories, so `**/x` matches a top-level `x`
                out.append("(?:[^/]+/)*")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if c == "?":
            out.append("[^/]")
            i += 1
            continue
        if c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:                       # unclosed bracket is a literal `[`
                out.append(re.escape(c))
                i += 1
                continue
            body = pattern[i + 1:j]
            if body[:1] in ("!", "^"):
                body = "^" + body[1:]
            out.append("[" + body + "]")
            i = j + 1
            continue
        out.append(re.escape(c))
        i += 1
    return "".join(out) + r"\Z"


def _compiled(pattern: str):
    rx = _CACHE.get(pattern)
    if rx is None:
        rx = re.compile(translate(pattern))
        _CACHE[pattern] = rx
    return rx


def normalize(path: str) -> str:
    path = path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def matches(path: str, pattern: str) -> bool:
    """Does one repo-relative path match one glob."""
    path = normalize(path)
    pattern = normalize(pattern)
    if _compiled(pattern).match(path):
        return True
    # A bare `*.css` is meant to mean "any css file", not "a css file in the root".
    if "/" not in pattern:
        return bool(_compiled(pattern).match(path.rsplit("/", 1)[-1]))
    return False


def first_match(paths, patterns):
    """The first (path, pattern) pair that matches, or None.

    The gate quotes this back in the deny text — "required because
    public/index.html matches **/*.html" — so the pair matters, not just a bool.
    """
    for pattern in patterns:
        for path in paths:
            if matches(path, pattern):
                return path, pattern
    return None
