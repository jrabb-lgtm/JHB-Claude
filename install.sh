#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/com.joehomebuyer.claude-sync.plist"
SCRIPT="$HOME/Documents/Claude/Projects/Python Daily List/sync.sh"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" << PEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.joehomebuyer.claude-sync</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>'${SCRIPT}'</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>'${HOME}/Library/Logs/jhb-sync.log'</string>
  <key>StandardErrorPath</key><string>'${HOME}/Library/Logs/jhb-sync.log'</string>
</dict></plist>
PEOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "LaunchAgent installed - syncing every 5 minutes"
