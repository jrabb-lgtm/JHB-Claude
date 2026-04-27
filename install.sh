#!/bin/bash
# Copies the fixed daily_list.py to ~/daily-list/
# Also updates the rdl alias to point to this workspace so future fixes are automatic

WORKSPACE="/Users/jordanrabb/Documents/Claude/Projects/Python Daily List"

# Copy fixed script
cp "$WORKSPACE/daily_list.py" ~/daily-list/daily_list.py && echo "✓ Fixed daily_list.py copied to ~/daily-list/" || echo "✗ Copy failed"

# Update rdl alias to always use workspace version (picks up future fixes automatically)
sed -i '' '/alias rdl=/d' ~/.zshrc
echo "alias rdl='python3 \"$WORKSPACE/daily_list.py\"'" >> ~/.zshrc
source ~/.zshrc 2>/dev/null || true
echo "✓ rdl alias updated"
