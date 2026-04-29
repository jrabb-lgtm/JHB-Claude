---
name: memory-sync
description: |
  Trigger this skill whenever new information needs to persist across sessions.
  Use when Jordan says "remember this", "save this", "add this to memory",
  or when important new context is learned during a session that should survive
  future resets. Also triggers after any direct edit to CLAUDE.md.
---

# Memory Sync

When new information needs to persist, update CLAUDE.md and push it to GitHub.
GitHub is the source of truth — sync.sh distributes it to all sessions automatically.

## Step 1: Update CLAUDE.md

Read the current CLAUDE.md using the Read tool:
```
/var/folders/mn/d3qkr2hj60gd36rd1f4n6x_r0000gn/T/claude-hostloop-plugins/925c72ee8652bb94/CLAUDE.md
```

Use the Edit tool to add or update the relevant section. Rules:
- Update existing sections rather than appending duplicates
- Keep it concise — CLAUDE.md is loaded into every session
- Never put secrets, tokens, or passwords in CLAUDE.md (it's in a public GitHub repo)

## Step 2: Push to GitHub via computer-use Terminal

Use computer-use to open Terminal on Jordan's Mac and run as a single command:

```bash
TOKEN=$(cat ~/.jhb_github_token) && REPO="$HOME/Documents/Claude/Projects/Python Daily List" && CLAUDE_SRC=$(find /var/folders -name "CLAUDE.md" -path "*/claude-hostloop-plugins/*" 2>/dev/null | head -1) && echo "Source: $CLAUDE_SRC" && cp "$CLAUDE_SRC" "$REPO/CLAUDE.md" && cd "$REPO" && git config user.email "jrabb@joehomebuyer.com" && git config user.name "Jordan Rabb" && git remote set-url origin "https://${TOKEN}@github.com/jrabb-lgtm/JHB-Claude.git" && git add CLAUDE.md && { git diff --cached --quiet && echo "No changes to commit" || git commit -m "Memory update $(date '+%Y-%m-%d %H:%M')"; } && git push origin main && echo "Memory pushed to GitHub"
```

Wait for "Memory pushed to GitHub" before confirming.

## Step 3: Confirm

Tell Jordan: "Saved to memory." One line, no fanfare.

## Notes
- Token lives at `~/.jhb_github_token` — never hardcode it here
- CLAUDE.md is public on GitHub — never write API keys, tokens, or passwords into it
- sync.sh runs every 5 minutes and distributes the updated CLAUDE.md to all active Cowork sessions automatically
- Seth pulls the updated CLAUDE.md from GitHub via the same sync
