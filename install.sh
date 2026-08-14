#!/usr/bin/env bash
# Wire shipgate into Claude Code. Idempotent: run it twice, nothing changes twice.
#
# It touches exactly two files. ~/.claude/settings.json gets a PreToolUse and a
# PostToolUse entry pointing at this checkout (backed up first, diff printed
# before anything is written). The repo you run it in gets `.shipgate/` appended
# to .gitignore. Nothing else, anywhere.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
CMD="python3 $HERE/shipgate/hook.py"

echo "shipgate installer"
echo "  package : $HERE"
echo "  settings: $SETTINGS"
echo "  command : $CMD"
echo

mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

BACKUP="$SETTINGS.shipgate-backup.$(date +%Y%m%d%H%M%S)"
cp "$SETTINGS" "$BACKUP"
echo "backed up to $BACKUP"

NEW="$(mktemp)"
python3 - "$SETTINGS" "$CMD" "$NEW" <<'PY'
import json, sys

settings_path, command, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(settings_path) as fh:
    text = fh.read().strip() or "{}"
settings = json.loads(text)

hooks = settings.setdefault("hooks", {})
for event in ("PreToolUse", "PostToolUse"):
    matchers = hooks.setdefault(event, [])
    entry = None
    for m in matchers:
        if m.get("matcher") == "Bash":
            entry = m
            break
    if entry is None:
        entry = {"matcher": "Bash", "hooks": []}
        matchers.append(entry)
    listed = entry.setdefault("hooks", [])
    # Idempotent on the command string, and on any older shipgate path, so
    # re-running from a moved checkout replaces rather than duplicates.
    listed[:] = [h for h in listed
                 if "shipgate/hook.py" not in str(h.get("command", ""))]
    listed.append({"type": "command", "command": command})

with open(out_path, "w") as fh:
    json.dump(settings, fh, indent=2)
    fh.write("\n")
PY

if diff -u "$SETTINGS" "$NEW" > /tmp/shipgate-diff.$$ 2>&1; then
  echo "settings already wired; nothing to change"
  rm -f "$NEW" /tmp/shipgate-diff.$$
else
  echo "--- change to $SETTINGS ---"
  cat /tmp/shipgate-diff.$$
  echo "---------------------------"
  mv "$NEW" "$SETTINGS"
  rm -f /tmp/shipgate-diff.$$
  echo "written"
fi

# Keep shipgate's own state out of the branch it is gating.
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$ROOT" ]; then
  IGNORE="$ROOT/.gitignore"
  if ! grep -qx '\.shipgate/\?' "$IGNORE" 2>/dev/null; then
    printf '\n# shipgate records which checks passed, per machine, not per branch\n.shipgate/\n' >> "$IGNORE"
    echo "appended .shipgate/ to $IGNORE"
  else
    echo ".shipgate/ already in $IGNORE"
  fi
fi

echo
echo "Next: cd into a repo you want gated and run"
echo "  python3 $HERE/shipgate init"
echo "then read the .shipgate.json it writes and cut what does not apply."
echo
echo "To uninstall, restore the backup above."
