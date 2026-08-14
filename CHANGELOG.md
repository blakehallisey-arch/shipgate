# Changelog

## 0.1.0 — first cut

- PreToolUse hook over ship commands, with the required checks derived from the
  diff rather than from a fixed list.
- Passes stamped with a git tree SHA of the working tree, so an edit made after
  a check ran makes that check come back.
- `status`, `run`, `pass`, `reset`, `init` on the CLI; `--json` on status.
- Per-ship reset, with a pending/settle pass so a merge that fails on a conflict
  does not burn the checks that were already satisfied.
- `.gitignore` and `.shipgate.json` sniffing in `install.sh` and `init`.
