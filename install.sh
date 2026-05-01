#!/bin/bash
# claude-tts installer for macOS.

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MODEL_DIR="$HOME/.local/share/kokoro-tts"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
KOKORO_PLIST="$LAUNCH_AGENTS/com.bmalan.kokoro-tts.plist"
SKHD_DIR="$HOME/.config/skhd"
CLAUDE_HOOKS="$HOME/.claude/hooks"
CLAUDE_COMMANDS="$HOME/.claude/commands"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
ok()   { printf "\033[32m[ok]\033[0m %s\n" "$*"; }
warn() { printf "\033[33m[warn]\033[0m %s\n" "$*"; }

bold "claude-tts installer"
echo "Project: $PROJECT_DIR"
echo

# 1. Homebrew dependencies
bold "Step 1: Homebrew dependencies"
command -v brew >/dev/null || { echo "Homebrew required. Install: https://brew.sh"; exit 1; }
for pkg in uv jq; do
  if ! command -v "$pkg" >/dev/null; then
    brew install "$pkg"
  fi
  ok "$pkg installed"
done
if ! command -v skhd >/dev/null; then
  brew install koekeishiya/formulae/skhd
fi
ok "skhd installed"
echo

# 2. Kokoro Python tool
bold "Step 2: kokoro-onnx Python tool"
if ! uv tool list 2>/dev/null | grep -q "^kokoro-onnx"; then
  uv tool install kokoro-onnx
fi
uv tool install --force --with sounddevice kokoro-onnx 2>/dev/null || true
ok "kokoro-onnx ready"
echo

# 3. Model files
bold "Step 3: Kokoro model files"
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
ok "Model files in $MODEL_DIR"
echo

# 4. Render launchd plist with current $HOME
bold "Step 4: launchd agent for Kokoro server"
mkdir -p "$LAUNCH_AGENTS"
sed "s|__HOME__|$HOME|g; s|__PROJECT__|$PROJECT_DIR|g" \
    "$PROJECT_DIR/launchd/com.bmalan.kokoro-tts.plist.template" \
    > "$KOKORO_PLIST" 2>/dev/null \
  || cat > "$KOKORO_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.bmalan.kokoro-tts</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/uv</string>
        <string>tool</string><string>run</string>
        <string>--from</string><string>kokoro-onnx</string>
        <string>--with</string><string>sounddevice</string>
        <string>python</string>
        <string>$PROJECT_DIR/server/kokoro_server.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/kokoro-server.log</string>
    <key>StandardErrorPath</key><string>/tmp/kokoro-server.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key><string>$HOME</string>
        <key>KOKORO_VOICE</key><string>af_sky</string>
    </dict>
</dict>
</plist>
EOF
launchctl unload "$KOKORO_PLIST" 2>/dev/null || true
launchctl load "$KOKORO_PLIST"
ok "Kokoro server agent loaded"
echo

# 5. skhd config + load
bold "Step 5: skhd hotkeys"
mkdir -p "$SKHD_DIR"
sed "s|__PROJECT__|$PROJECT_DIR|g" "$PROJECT_DIR/skhd/skhdrc" > "$SKHD_DIR/skhdrc"
SKHD_PLIST="$LAUNCH_AGENTS/com.koekeishiya.skhd.plist"
if [ ! -f "$SKHD_PLIST" ]; then
  skhd --start-service 2>/dev/null || true
fi
launchctl unload "$SKHD_PLIST" 2>/dev/null || true
launchctl load "$SKHD_PLIST" 2>/dev/null || true
ok "skhd loaded with hotkeys: Cmd+Option+S (speak), Cmd+Option+X (stop)"
echo

# 6. Claude Code wiring
bold "Step 6: Claude Code Stop hook + /speak command"
mkdir -p "$CLAUDE_COMMANDS"
cp "$PROJECT_DIR/claude-config/speak.md" "$CLAUDE_COMMANDS/speak.md"
sed -i.bak "s|__PROJECT__|$PROJECT_DIR|g" "$CLAUDE_COMMANDS/speak.md" 2>/dev/null || true
rm -f "$CLAUDE_COMMANDS/speak.md.bak"

if [ -f "$CLAUDE_SETTINGS" ]; then
  jq --arg cmd "bash $PROJECT_DIR/scripts/tts-capture.sh" '
    .hooks.Stop = [{"hooks": [{"type": "command", "command": $cmd, "timeout": 10}]}]
  ' "$CLAUDE_SETTINGS" > "$CLAUDE_SETTINGS.tmp" && mv "$CLAUDE_SETTINGS.tmp" "$CLAUDE_SETTINGS"
else
  cat > "$CLAUDE_SETTINGS" <<EOF
{ "hooks": { "Stop": [{ "hooks": [{ "type": "command", "command": "bash $PROJECT_DIR/scripts/tts-capture.sh", "timeout": 10 }] }] } }
EOF
fi
ok "Claude Code wired"
echo

bold "Done!"
echo "First time you press Cmd+Option+S, macOS will prompt for Accessibility permission for skhd."
echo "Grant it once and the hotkey works system-wide."
