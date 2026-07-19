#!/bin/bash
# backup_captures.sh — automatic off-machine backup of data that exists
# nowhere else.
#
# Scope (owner call, 2026-07-18): the Timing71 replay zips (IMSA Archives/,
# wec-archives/) are NOT backed up — they can be re-downloaded from Timing71.
# What IS backed up, because no one can recreate it after a disk loss:
#   - our own Griiip --record captures   data/*.jsonl.gz
#   - evaluator reports cited in BACKLOG logs/eval_*.txt
# race.db is excluded on purpose: it's hot (SQLite WAL) during races — syncing
# it mid-write risks a corrupt copy — and it's rebuildable from the captures.
#
#   ./tools/backup_captures.sh            one backup pass now
#   ./tools/backup_captures.sh --install  backup now AND install a LaunchAgent
#                                         that re-runs this daily + at login
#
# Target: iCloud Drive / "Overcut Backups" (macOS syncs it off-machine
# automatically; nothing else to configure).

set -euo pipefail
shopt -s nullglob
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Overcut Backups"
PLIST="$HOME/Library/LaunchAgents/com.overcut.backup.plist"

mkdir -p "$TARGET/captures" "$TARGET/eval-logs"

caps=("$REPO/data/"*.jsonl.gz)
logs=("$REPO/logs/"eval_*.txt)
[ ${#caps[@]} -gt 0 ] && rsync -a "${caps[@]}" "$TARGET/captures/"
[ ${#logs[@]} -gt 0 ] && rsync -a "${logs[@]}" "$TARGET/eval-logs/"
echo "$(date '+%Y-%m-%d %H:%M:%S')  ${#caps[@]} captures, ${#logs[@]} eval logs" >> "$TARGET/backup.log"
echo "backed up: ${#caps[@]} captures, ${#logs[@]} eval logs -> $TARGET"

if [ "${1:-}" = "--install" ]; then
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.overcut.backup</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO/tools/backup_captures.sh</string>
  </array>
  <key>StartInterval</key><integer>86400</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$TARGET/backup.err</string>
</dict>
</plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "LaunchAgent installed: daily + at login (com.overcut.backup)"
fi
