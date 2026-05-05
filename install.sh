#!/bin/bash
# Claudible installer for macOS.
#
# Builds the menu bar app, copies it to /Applications, downloads the Kokoro
# model files if missing, wires the Claude Code Stop hook so the app receives
# capture signals.

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MODEL_DIR="$HOME/.local/share/kokoro-tts"
CLAUDE_COMMANDS="$HOME/.claude/commands"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
APP_DEST="/Applications/Claudible.app"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m[ok]\033[0m %s\n" "$*"; }

bold "Claudible installer"
echo "Project: $PROJECT_DIR"
echo

# 1. Homebrew + python3 + uv
bold "Step 1: dependencies"
command -v brew >/dev/null || { echo "Homebrew required: https://brew.sh"; exit 1; }
for pkg in jq python@3.12; do
  brew list "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done
ok "brew deps"

# 2. Kokoro model files
bold "Step 2: Kokoro model files"
mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_DIR/kokoro-v1.0.onnx" ]; then
  echo "Downloading kokoro-v1.0.onnx (310 MB)..."
  curl -L -o "$MODEL_DIR/kokoro-v1.0.onnx" \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/latest/download/kokoro-v1.0.onnx"
fi
if [ ! -f "$MODEL_DIR/voices-v1.0.bin" ]; then
  echo "Downloading voices-v1.0.bin (27 MB)..."
  curl -L -o "$MODEL_DIR/voices-v1.0.bin" \
    "https://github.com/thewh1teagle/kokoro-onnx/releases/latest/download/voices-v1.0.bin"
fi
ok "Models in $MODEL_DIR"

# 3. Build the .app bundle
bold "Step 3: Build Claudible.app"
cd "$PROJECT_DIR/app"
PY=$(brew --prefix python@3.12)/bin/python3.12
"$PY" -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt py2app
rm -rf build dist
python setup.py py2app >/dev/null
ok "Built dist/Claudible.app"

# 4. Move to /Applications
bold "Step 4: Install to /Applications"
rm -rf "$APP_DEST"
cp -R dist/Claudible.app "$APP_DEST"
ok "Installed at $APP_DEST"

# 5. Wire Claude Code Stop hook
bold "Step 5: Claude Code Stop hook + /speak slash command"
mkdir -p "$CLAUDE_COMMANDS"
cp "$PROJECT_DIR/claude-config/speak.md" "$CLAUDE_COMMANDS/speak.md"
HOOK_CMD="bash $PROJECT_DIR/scripts/tts-capture.sh"
if [ -f "$CLAUDE_SETTINGS" ]; then
  jq --arg cmd "$HOOK_CMD" '
    .hooks.Stop = [{"hooks": [{"type": "command", "command": $cmd, "timeout": 10}]}]
  ' "$CLAUDE_SETTINGS" > "$CLAUDE_SETTINGS.tmp" && mv "$CLAUDE_SETTINGS.tmp" "$CLAUDE_SETTINGS"
else
  cat > "$CLAUDE_SETTINGS" <<EOF
{ "hooks": { "Stop": [{ "hooks": [{ "type": "command", "command": "$HOOK_CMD", "timeout": 10 }] }] } }
EOF
fi
ok "Stop hook wired"

echo
bold "Done."
echo "Open $APP_DEST. The first time you press Cmd+Shift+S, macOS will prompt"
echo "for Accessibility permission. Grant it once and global hotkeys are live:"
echo "  Cmd+Shift+S -> speak Claude's last response (toggle)"
echo "  Cmd+Shift+H -> speak the highlighted text"
