# claude-tts

Hands-free Claude Code: read Claude's last response aloud with a global hotkey, using local neural TTS (Kokoro).

- 100% local, no API keys, no internet required for inference
- Subsecond first-audio after the first cached sentence (sentence-level pre-fetch + on-disk cache)
- af_sky voice by default; 54 voices available
- Hotkey works from any app, not just Claude Code

## How it works

1. **Capture hook** (`scripts/tts-capture.sh`) - Claude Code Stop hook that silently writes the last assistant response to `/tmp/claude-last-response.txt` after every turn.
2. **Kokoro server** (`server/kokoro_server.py`) - long-running launchd daemon that loads the Kokoro ONNX model into RAM once. Exposes `POST /play`, `POST /stop`, `GET /health`. Worker thread chunks text into sentences, pre-fetches the next sentence while the current one plays, and caches synthesized WAVs by content hash to `/tmp/kokoro-cache/`.
3. **Hotkeys via skhd** (`skhd/skhdrc`) - global keyboard shortcuts:
   - `Cmd + Option + S` -> POST `/play` with the captured text (interrupts in-flight playback)
   - `Cmd + Option + X` -> POST `/stop`
4. **`/speak` slash command** (`claude-config/speak.md`) - same thing as the hotkey but invokable from inside Claude Code.

## Requirements

- macOS (Apple Silicon recommended)
- Homebrew
- ~1.2 GB free RAM for the loaded Kokoro model
- ~340 MB disk for the model files

## Install

```bash
git clone https://github.com/<you>/claude-tts.git ~/Projects/claude-tts
cd ~/Projects/claude-tts
./install.sh
```

The installer:
- Installs `uv`, `skhd`, `jq` via Homebrew (if missing)
- Installs `kokoro-onnx` Python tool via uv
- Downloads the Kokoro model files (~340 MB) to `~/.local/share/kokoro-tts/`
- Templates the launchd plist and installs it to `~/Library/LaunchAgents/`
- Drops `skhdrc` into `~/.config/skhd/`
- Wires the Claude Code Stop hook into `~/.claude/settings.json`
- Drops `speak.md` into `~/.claude/commands/`
- Loads both launchd agents (Kokoro server + skhd)

After install, press `Cmd + Option + S` once. macOS will prompt for Accessibility permission for skhd. Grant it and you're done.

## Configure

- **Voice**: edit `KOKORO_VOICE` in `~/Library/LaunchAgents/com.bmalan.kokoro-tts.plist`. Reload with `launchctl unload ... && launchctl load ...`. List of voices: see `kokoro-onnx` docs.
- **Hotkeys**: edit `skhd/skhdrc` and run `launchctl kickstart -k gui/$(id -u)/com.koekeishiya.skhd`.
- **Trigger**: tweak the `Cmd + Option + S` line in `skhd/skhdrc`.

## Files

```
server/kokoro_server.py        # the long-running TTS daemon
scripts/speak-last.sh          # POST /play; called by hotkey + /speak
scripts/speak-stop.sh          # POST /stop
scripts/tts-capture.sh         # Stop hook: writes /tmp/claude-last-response.txt
launchd/com.bmalan.kokoro-tts.plist
skhd/skhdrc                    # global hotkey config
claude-config/speak.md         # /speak slash command
desktop/test-speak.command     # standalone tester (Terminal)
install.sh
```

## Why not just use Apple Speak Selection (Option+Esc)?

Apple's built-in Speak Selection works great with Siri voices, but it requires you to manually select text first. This tool is a one-keypress trigger that always reads Claude's last response, no selection needed. Use both - they're complementary.
