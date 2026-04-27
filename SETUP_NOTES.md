# Mac Mini Setup Notes

## Apps Script Web App
Deployed: April 22, 2026
URL: https://script.google.com/macros/s/AKfycbx2nAPJS1yXZ1EaWge49JHkk80XQdtpFk9-ybNoPK0rqKPghCRZMCDgnQFoUCRShBXkPQ/exec

## Environment Variables to Set on Mac Mini
Run these in Terminal once the Mac Mini arrives:

```bash
# Apps Script URL (already deployed)
echo 'export APPS_SCRIPT_URL="https://script.google.com/macros/s/AKfycbx2nAPJS1yXZ1EaWge49JHkk80XQdtpFk9-ybNoPK0rqKPghCRZMCDgnQFoUCRShBXkPQ/exec"' >> ~/.zshrc

# Pick a strong random secret and set it on BOTH sides:
# 1. Here in ~/.zshrc:
echo 'export APPS_SCRIPT_SECRET="YOUR_SECRET_HERE"' >> ~/.zshrc

# 2. In Apps Script editor → Project Settings → Script Properties:
#    Key: APPS_SCRIPT_SECRET  Value: YOUR_SECRET_HERE

# Anthropic API key (from console.anthropic.com)
echo 'export ANTHROPIC_API_KEY="YOUR_KEY_HERE"' >> ~/.zshrc

# Optional: 2Captcha key for auto-solving reCAPTCHA
echo 'export TWOCAPTCHA_API_KEY="YOUR_KEY_HERE"' >> ~/.zshrc

source ~/.zshrc
```

## Install Python Dependencies
```bash
pip3 install anthropic playwright pymupdf requests --break-system-packages
python3 -m playwright install chromium
```

## Copy Script
```bash
cp ~/path/to/daily_list.py ~/daily-list/daily_list.py
```
