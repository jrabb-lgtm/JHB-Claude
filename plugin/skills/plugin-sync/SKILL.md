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

After any skill is created or updated, rebuild the joe-homebuyer plugin and save it to Google Drive so the latest version is always available for the team.

## What to do

Run these steps immediately after finishing any skill work:

### Step 1: Build a fresh plugin directory in /tmp

```bash
rm -rf /tmp/joe-homebuyer-build
mkdir -p /tmp/joe-homebuyer-build/.claude-plugin
mkdir -p /tmp/joe-homebuyer-build/skills
```

### Step 2: Write the plugin manifest

```bash
cat > /tmp/joe-homebuyer-build/.claude-plugin/plugin.json << 'EOF'
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

### Step 3: Copy all current skills into the build

**Source of truth is the remote plugin directory — do NOT pull from `/mnt/.claude/skills/`.** The remote plugin path looks like:
```
/sessions/.../mnt/.remote-plugins/plugin_01CFoyptHFVebWkN4oer2qZU/skills/
```

Copy every skill folder from that directory into `/tmp/joe-homebuyer-build/skills/`:
```bash
cp -r /sessions/.../mnt/.remote-plugins/plugin_01CFoyptHFVebWkN4oer2qZU/skills/* /tmp/joe-homebuyer-build/skills/
chmod -R u+w /tmp/joe-homebuyer-build
```

Then apply any edits you made this session on top of those copies.

If a brand-new skill was created this session (not yet in the remote plugin), copy it from wherever it was written (e.g. `/tmp/` or `/mnt/outputs/`) into `/tmp/joe-homebuyer-build/skills/` as well.

**Never copy from `/mnt/.claude/skills/`** — those are system/shared skills (pptx, pdf, docx, etc.) and do not belong in the joe-homebuyer plugin.

### Step 4: Package and save to Google Drive

The session name in the path changes every session — never hardcode it. Use `find` to locate the mount dynamically:

```bash
cd /tmp/joe-homebuyer-build && \
zip -r /tmp/joe-homebuyer.plugin . -x "*.DS_Store"

# Find Google Drive mount dynamically (session name changes every session)
DRIVE_DIR=$(find /sessions/*/mnt/CloudStorage/ -maxdepth 3 -type d -name "Claude" 2>/dev/null | grep "GoogleDrive-jrabb" | head -1)

if [ -n "$DRIVE_DIR" ]; then
  cp /tmp/joe-homebuyer.plugin "$DRIVE_DIR/joe-homebuyer.plugin" && echo "Saved to Google Drive: $DRIVE_DIR"
else
  # Fallback: try iCloud
  ICLOUD_DIR=$(find /sessions/*/mnt/com~apple~CloudDocs/ -maxdepth 1 -type d -name "Claude" 2>/dev/null | head -1)
  if [ -n "$ICLOUD_DIR" ]; then
    cp /tmp/joe-homebuyer.plugin "$ICLOUD_DIR/joe-homebuyer.plugin" && echo "Saved to iCloud: $ICLOUD_DIR"
  else
    # Last resort: save to outputs so user can manually place it
    cp /tmp/joe-homebuyer.plugin "/sessions/$(ls /sessions/ | head -1)/mnt/outputs/joe-homebuyer.plugin"
    echo "WARNING: Google Drive and iCloud not accessible. Saved to outputs folder — please upload manually."
  fi
fi
```

### Step 5: Confirm

Tell the user where the plugin was saved (Google Drive, iCloud, or outputs). One line, no fanfare.

## Notes

- The session name (e.g. `serene-youthful-shannon`) changes every session — always use `find` to locate the mount, never hardcode the session name
- The Google Drive path pattern is: `.../mnt/CloudStorage/GoogleDrive-jrabb@joehomebuyer.com/My Drive/Claude/joe-homebuyer.plugin`
- Direct file writes to Google Drive subdirectories fail due to FUSE limitations — only the top-level `Claude/` folder accepts writes
- Always overwrite the existing file — do not version or rename it
- Before deleting any row from the Google Sheet, always take a screenshot first to confirm the row actually exists — never assume data was written
