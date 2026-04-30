---
name: plugin-sync
description: |
  ALWAYS trigger this skill as the final step after creating, editing, or updating any skill
  in a Joe Homebuyer session. This includes: adding a new skill, modifying an existing SKILL.md,
  improving a skill based on feedback, or combining skills. Any time the phrase "update the skill",
  "save the skill", "add this to the skill", or "create a skill" is used and work is completed,
  run this sync automatically — do not wait to be asked.
---

# Plugin Sync

After any skill is created or updated, sync all skills to GitHub.

## GitHub details

- **Repo:** `jrabb-lgtm/JHB-Claude`
- **Token file:** `~/.jhb_github_token` (on Jordan's Mac)
- **Git repo on Mac:** `~/jhb-repo` (actual clone — not a symlink, not ~/Documents)

## What to do

### Step 1: Copy skills from the plugin to the workspace folder (bash sandbox)

**Source of truth is the remote plugin directory — do NOT pull from `/mnt/.claude/skills/`.**

```bash
REMOTE=$(find /sessions/ -type d -name "plugin_01CFoyptHFVebWkN4oer2qZU" 2>/dev/null | head -1)
WORKSPACE=$(find /sessions/ -maxdepth 3 -type d -name "Python Daily List" 2>/dev/null | head -1)

for skill in daily-list plugin-sync tax-foreclosure-research; do
  mkdir -p "$WORKSPACE/skills/$skill"
  cat "$REMOTE/skills/$skill/SKILL.md" > "$WORKSPACE/skills/$skill/SKILL.md"
done
echo "Skills written to workspace"
```

Apply any edits made this session on top. If a brand-new skill was created, add it to the loop.

**Never copy from `/mnt/.claude/skills/`** — those are system/shared skills and don't belong here.

### Step 2: Push to GitHub via Terminal (computer-use)

Use `mcp__computer-use__request_access` for Terminal, then run:

```bash
TOKEN=$(cat ~/.jhb_github_token)
cd ~/jhb-repo
git remote set-url origin "https://${TOKEN}@github.com/jrabb-lgtm/JHB-Claude.git"

# Sync skills and CLAUDE.md from workspace into the repo
cp -r ~/Documents/Claude/Projects/Python\ Daily\ List/skills/. skills/
cp ~/Documents/Claude/Projects/Python\ Daily\ List/CLAUDE.md .

git add -A
git diff --cached --quiet && echo "Nothing new to push" || (git commit -m "Update skills - $(date '+%Y-%m-%d %H:%M')" && git push origin main && echo "Pushed!")
```

### Step 3: Confirm

One line to the user: `Plugin pushed to GitHub (jrabb-lgtm/JHB-Claude).`

## Notes

- Token lives at `~/.jhb_github_token` — never hardcode it or commit it
- Always push from `~/jhb-repo` (not from ~/Documents) — avoids getcwd() errors in background processes
- Terminal has Full Disk Access and can read from ~/Documents — this is why the cp works
- Cron (`jhb-push-once.sh`) runs every minute as a backup retry — it does the same cp+push but only if there's something staged
- Before deleting any row from the Google Sheet, always take a screenshot first to confirm the row actually exists — never assume data was written
