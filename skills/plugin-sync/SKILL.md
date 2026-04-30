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

After any skill is created or updated, write the changes directly to the connected workspace folder. The cron job handles the GitHub push automatically — no computer control needed.

---

## Routing Rules — What Goes Where

Before saving anything, pick the right file:

| Content type | Save to |
|---|---|
| Tax foreclosure research logic, flagging rules, case examples, investment strategy, HOT/WARM/WATCH criteria | `skills/tax-foreclosure-research/SKILL.md` |
| Daily list counties, probate sheet updates, daily list Python script notes | `skills/daily-list/SKILL.md` |
| How to sync skills or push to GitHub | `skills/plugin-sync/SKILL.md` |
| Global facts about Jordan that apply across ALL sessions (email, company, global preferences) | `CLAUDE.md` in the workspace root |

**Never put skill-specific knowledge in CLAUDE.md.** That file is for truly global memory only.

---

## Step 1 — Edit the skill file directly (no computer control)

The `Python Daily List` folder is the stable home for all skills. When it is connected as a Cowork workspace, use the `Edit` or `Write` file tools directly:

**Workspace skill paths:**
```
/Users/jordanrabb/Documents/Claude/Projects/Python Daily List/skills/daily-list/SKILL.md
/Users/jordanrabb/Documents/Claude/Projects/Python Daily List/skills/plugin-sync/SKILL.md
/Users/jordanrabb/Documents/Claude/Projects/Python Daily List/skills/tax-foreclosure-research/SKILL.md
```

For a small addition (e.g. a new case example or strategy note), use `Edit` to append or insert.
For a full rewrite, use `Write` to replace the whole file.

**If the Python Daily List folder is NOT connected this session**, ask Jordan:
> "To save this directly, I need the Python Daily List folder connected. You can add it in Cowork by clicking the folder icon and selecting Documents → Claude → Projects → Python Daily List."

Do NOT fall back to TextEdit or computer control — just ask Jordan to connect the folder.

---

## Step 2 — GitHub push (automatic)

The cron job `jhb-push-once.sh` runs every minute on Jordan's Mac. It copies from this folder to `~/jhb-repo` and pushes to `jrabb-lgtm/JHB-Claude` automatically.

**No action needed.** Just wait ~60 seconds after saving.

If an immediate push is needed, run this from the bash sandbox:

```bash
ls "/sessions/optimistic-eloquent-heisenberg/mnt/Python Daily List/skills/"
```

If the mount is present (confirms the folder is connected), the cron will handle it. If you need to force it NOW, write a push script to outputs and double-click from Finder:

```bash
cat > /sessions/optimistic-eloquent-heisenberg/mnt/outputs/jhb_push.command << 'EOF'
#!/bin/bash
TOKEN=$(cat ~/.jhb_github_token)
cd ~/jhb-repo
git remote set-url origin "https://${TOKEN}@github.com/jrabb-lgtm/JHB-Claude.git"
cp -r ~/Documents/Claude/Projects/Python\ Daily\ List/skills/. skills/
git add -A
git diff --cached --quiet && echo "Nothing new to push" || (git commit -m "Update skills - $(date '+%Y-%m-%d %H:%M')" && git push origin main && echo "Pushed!")
EOF
chmod +x /sessions/optimistic-eloquent-heisenberg/mnt/outputs/jhb_push.command
echo "Script ready — open Finder → outputs folder → double-click jhb_push.command"
```

---

## Step 3 — Confirm

Tell Jordan: `Saved to [skill name]. GitHub will sync within a minute.`

---

## Notes

- The `Python Daily List` folder contains ALL skills — not just the daily list
- The folder connection may need to be re-added each session (Cowork doesn't yet persist it automatically)
- If the session mount path has changed (different session name), update the bash path above
- Token lives at `~/.jhb_github_token` — never hardcode it
- Always push from `~/jhb-repo`, not from ~/Documents — avoids getcwd() errors
