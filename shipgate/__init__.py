"""shipgate — a ship gate that reads the diff.

A Claude Code PreToolUse hook that stands in front of the commands that actually
ship, and refuses until the checks this particular change needs have run. The
required checks come from the diff, not from a fixed list, so a docs-only merge
goes through in silence and a merge carrying a page a human looks at does not.

Standard library only, Python 3.9+. No network, no telemetry, no account.
"""

__version__ = "0.1.0"
