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

After any skill is created or updated, rebuild the joe-homebuyer plugin and push it to GitHub.

## GitHub details

- **Repo:** `jrabb-lgtm/JHB-Claude`
- **Token file:** `~/.jhb_github_token` (on Jordan's Mac — read via computer-use Terminal)
- **Plugin file path in repo:** `joe-homebuyer.plugin`

## What to do

### Step 1: Build a fresh plugin directory

```bash
BUILD="/sessions/$(ls /sessions/ | head -1)/mnt/outputs/joe-homebuyer-build"
rm -rf "$BUILD"
mkdir -p "$BUILD/.claude-plugin"
mkdir -p "$BUILD/skills"

cat > "$BUILD/.claude-plugin/plugin.json" << 'EOF'
{
  "name": "joe-homebuyer",
  "version": "0.1.0",
  "description": "Joe Homebuyer workflows — probate case research, Google Sheet updates, and more.",
  "author": {
    "name": "Joe Homebuyer"
  }
}
EOF
```

### Step 2: Copy all current skills into the build

**Source of truth is the remote plugin directory — do NOT pull from `/mnt/.claude/skills/`.**

```bash
REMOTE=$(find /sessions/ -type d -name "plugin_01CFoyptHFVebWkN4oer2qZU" 2>/dev/null | head -1)
# Skills are read-only — use cat to copy each one
for skill in daily-list plugin-sync tax-foreclosure-research; do
  mkdir -p "$BUILD/skills/$skill"
  cat "$REMOTE/skills/$skill/SKILL.md" > "$BUILD/skills/$skill/SKILL.md"
done
```

Apply any edits made this session on top. If a brand-new skill was created this session, copy it in too.

**Never copy from `/mnt/.claude/skills/`** — those are system/shared skills and don't belong in this plugin.

### Step 3: Package the plugin

```bash
cd "$BUILD" && zip -r /tmp/joe-homebuyer.plugin . -x "*.DS_Store"
echo "Plugin built: $(du -h /tmp/joe-homebuyer.plugin | cut -f1)"
```

### Step 4: Push to GitHub via Terminal (computer-use)

Use `mcp__computer-use__request_access` for Terminal, then run:

```bash
TOKEN=$(cat ~/.jhb_github_token)
REPO_DIR=~/Documents/Claude/Projects/Python\ Daily\ List

cd "$REPO_DIR"
git remote set-url origin "https://${TOKEN}@github.com/jrabb-lgtm/JHB-Claude.git"

# Copy the built plugin into the repo
cp /tmp/joe-homebuyer.plugin "$REPO_DIR/joe-homebuyer.plugin"

git add joe-homebuyer.plugin
git commit -m "Update joe-homebuyer plugin - $(date '+%Y-%m-%d %H:%M')"
git push origin main
```

### Step 5: Confirm

One line to the user: `Plugin pushed to GitHub (jrabb-lgtm/JHB-Claude).`

## Notes

- Token lives at `~/.jhb_github_token` on Jordan's Mac — never hardcode it or commit it
- The repo is public — never put the token inside any file that gets committed
- Always overwrite `joe-homebuyer.plugin` — do not version or rename it
- Before deleting any row from the Google Sheet, always take a screenshot first to confirm the row actually exists — never assume data was written
