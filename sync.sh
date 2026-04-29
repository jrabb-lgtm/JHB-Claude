#!/bin/bash
# sync.sh — Nightly GitHub sync for Joe Homebuyer Claude setup
# Pulls latest from GitHub, updates daily_list.py, plugin, and all CLAUDE.md files

REPO_DIR="$HOME/Documents/Claude/Projects/Python Daily List"
LOG="$HOME/claude-sync.log"
TOKEN_FILE="$HOME/.jhb_github_token"

echo "" >> "$LOG"
echo "=== $(date) ===" >> "$LOG"

# Read token
if [ ! -f "$TOKEN_FILE" ]; then
    echo "ERROR: Token file not found at $TOKEN_FILE" >> "$LOG"
    exit 1
fi
TOKEN=$(cat "$TOKEN_FILE")

# Push any memory Seth has written to seth-memory/
cd "$REPO_DIR" || { echo "ERROR: Repo dir not found" >> "$LOG"; exit 1; }
git remote set-url origin "https://${TOKEN}@github.com/jrabb-lgtm/JHB-Claude.git"
git add seth-memory/ >> "$LOG" 2>&1
if ! git diff --cached --quiet; then
    git commit -m "Seth memory sync $(date '+%Y-%m-%d')" >> "$LOG" 2>&1
    echo "✓ Seth memory committed" >> "$LOG"
else
    echo "  (no new Seth memory to commit)" >> "$LOG"
fi

# Pull latest from GitHub (rebase keeps Seth's memory commit on top)
git pull --rebase origin main >> "$LOG" 2>&1
echo "✓ Git pull complete" >> "$LOG"

# Push Seth's memory commit if there was one
git push origin main >> "$LOG" 2>&1
echo "✓ Git push complete" >> "$LOG"

# Update daily_list.py
mkdir -p ~/daily-list
cp "$REPO_DIR/daily_list.py" ~/daily-list/daily_list.py
echo "✓ daily_list.py updated" >> "$LOG"

# Update CLAUDE.md in all active Cowork session paths
SESSIONS_BASE="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"
UPDATED=0
while IFS= read -r -d '' f; do
    cp "$REPO_DIR/CLAUDE.md" "$f"
    echo "✓ CLAUDE.md updated: $f" >> "$LOG"
    UPDATED=$((UPDATED + 1))
done < <(find "$SESSIONS_BASE" -maxdepth 6 -name "CLAUDE.md" -print0 2>/dev/null)

if [ "$UPDATED" -eq 0 ]; then
    echo "  (no existing CLAUDE.md session paths found — will seed on next Cowork session)" >> "$LOG"
fi

echo "✓ Sync complete" >> "$LOG"
