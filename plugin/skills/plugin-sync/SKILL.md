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

After any skill is created or updated, build the plugin and push it to GitHub. GitHub is the single source of truth.

## Step 1: Build a fresh plugin directory in /tmp

```bash
rm -rf /tmp/joe-homebuyer-build
mkdir -p /tmp/joe-homebuyer-build/.claude-plugin
mkdir -p /tmp/joe-homebuyer-build/skills

cat > /tmp/joe-homebuyer-build/.claude-plugin/plugin.json << 'EOF'
{
  "name": "joe-homebuyer",
  "version": "0.1.0",
  "description": "Joe Homebuyer workflows — daily list, tax foreclosure research, Quo call reports.",
  "author": { "name": "Joe Homebuyer" }
}
EOF
```

## Step 2: Copy all current skills into the build

Source of truth is the installed remote plugin directory. Find it dynamically:

```bash
PLUGIN_DIR=$(find /sessions/*/mnt/.remote-plugins/plugin_01CFoyptHFVebWkN4oer2qZU/skills -maxdepth 0 2>/dev/null | head -1)
cp -r "$PLUGIN_DIR"/* /tmp/joe-homebuyer-build/skills/
chmod -R u+w /tmp/joe-homebuyer-build
```

Apply any edits made this session on top of those copies. If a new skill was created this session, copy it into `/tmp/joe-homebuyer-build/skills/` as well.

**Never copy from `/mnt/.claude/skills/`** — those are system skills and don't belong here.

## Step 3: Package the plugin

```bash
cd /tmp/joe-homebuyer-build && zip -r /tmp/joe-homebuyer.plugin . -x "*.DS_Store"
```

## Step 4: Stage to Google Drive (transfer mechanism only)

```bash
DRIVE_DIR=$(find /sessions/*/mnt/CloudStorage/ -maxdepth 3 -type d -name "Claude" 2>/dev/null | grep "GoogleDrive-jrabb" | head -1)
if [ -n "$DRIVE_DIR" ]; then
  cp /tmp/joe-homebuyer.plugin "$DRIVE_DIR/joe-homebuyer.plugin"
  echo "Staged to Google Drive"
else
  echo "WARNING: Google Drive not found — cannot transfer to Mac for GitHub push."
fi
```

## Step 5: Push to GitHub via computer-use Terminal

Use computer-use to open Terminal on Jordan's Mac and run this as a single command:

```bash
REPO="$HOME/Documents/Claude/Projects/Python Daily List" && TOKEN=$(cat ~/.jhb_github_token) && GDRIVE="$HOME/Library/CloudStorage/GoogleDrive-jrabb@joehomebuyer.com/My Drive/Claude" && cp "$GDRIVE/joe-homebuyer.plugin" "$REPO/joe-homebuyer.plugin" && rm -rf /tmp/jhb-unzip && mkdir -p /tmp/jhb-unzip && cd /tmp/jhb-unzip && unzip -o "$REPO/joe-homebuyer.plugin" -d unpacked && rm -rf "$REPO/plugin" && cp -r /tmp/jhb-unzip/unpacked/. "$REPO/plugin/" && cd "$REPO" && git config user.email "jrabb@joehomebuyer.com" && git config user.name "Jordan Rabb" && git remote set-url origin "https://${TOKEN}@github.com/jrabb-lgtm/JHB-Claude.git" && git add plugin/ joe-homebuyer.plugin && { git diff --cached --quiet || git commit -m "Skill update $(date '+%Y-%m-%d %H:%M')"; } && git push origin main && echo "Pushed to GitHub"
```

Wait for "Pushed to GitHub" before confirming.

## Step 6: Confirm

Tell Jordan: "Pushed to GitHub." One line, no fanfare.

## Notes
- Token lives at `~/.jhb_github_token` on Jordan's Mac — never hardcode it in this file
- Google Drive is a staging area only — not a backup or destination
- Repo path: `~/Documents/Claude/Projects/Python Daily List`
- Never copy from `/mnt/.claude/skills/` — system skills don't belong in this plugin
