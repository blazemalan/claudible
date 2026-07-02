<p align="center">
  <img src="app/icon.png" alt="Claudible" width="160" />
</p>

# Claudible

A small macOS menu bar app that reads Claude Code's last response - or any text you highlight - aloud with one global hotkey.

Claude + audible.

- 100% local. No API keys, no internet at runtime.
- Lives in your menu bar. Quits cleanly. Clears its cache on quit.
- Pre-synthesizes Claude's response while you're still reading the screen, so when you press the hotkey, audio starts in under 200 ms.
- Speaks the text you've highlighted in any app - selection is read via the Accessibility API when the app exposes it, otherwise by briefly copying it (your clipboard is restored right after). If nothing is highlighted, it reads what's on your clipboard.
- Optional auto-speak: responses start reading themselves the moment Claude finishes.
- Frees the model's ~1.3 GB of RAM after 15 idle minutes; the next speak reloads it on demand.

## Hotkeys

**Cmd + Option + S** - speak Claude's last response. Press again to stop.

**Cmd + Option + A** - speak the text you've highlighted in the frontmost app, or the clipboard contents if nothing is highlighted. Press again to stop.

Both actions are also buttons in the menu bar menu ("Speak last" / "Speak selection / clipboard").

## Install

```bash
git clone https://github.com/blazemalan/claudible.git
cd claudible
./install.sh
```

Then open `Claudible.app` from `/Applications/`.

### Grant Accessibility permission (required for the hotkeys and Speak selection)

The global hotkeys - and reading the highlighted text out of other apps - need macOS **Accessibility** permission. On first launch Claudible asks macOS to show the "allow control" prompt - click **Open System Settings** and enable **Claudible**, then **relaunch the app**.

If no prompt appears (macOS doesn't always show it), grant it manually:

1. **System Settings → Privacy & Security → Accessibility**
2. Enable **Claudible** (or click **+** and add `/Applications/Claudible.app`)
3. **Quit and relaunch** Claudible

Until this is granted, the global hotkeys do nothing - the app logs the reason to `/tmp/claudible.log`. (The menu bar buttons still work; without the permission, "Speak selection / clipboard" can only read the clipboard, not the highlight.) Note: because the app is ad-hoc signed, macOS may drop this permission after a rebuild/reinstall, so you may need to re-enable it.

The installer:

- Downloads the Kokoro fp16 model (~196 MB total) to `~/.local/share/kokoro-tts/`
- Builds the `.app` bundle with `py2app`
- Copies it to `/Applications/Claudible.app`
- Wires a Claude Code Stop hook so the app gets a "prefetch" signal as soon as Claude finishes a response

## How it works

```
Claude Code finishes a response
        |
        v
[Stop hook] writes /tmp/claude-last-response.txt
        |
        v
[Stop hook] sends "prefetch" over /tmp/claudible.sock
        |
        v
[Claudible.app] synthesizes and caches the first chunk
        |
        v
You press Cmd+Option+S
        |
        v
[Claudible.app / pynput] calls _toggle_speak()
        |
        v
Audio starts ~100 ms later (cache hit), continues sentence-by-sentence
with each next chunk synthesized while the current one plays.
```

## Project layout

```
app/
  main.py              # the whole app: rumps menu bar + Kokoro pipeline
  setup.py             # py2app config
  requirements.txt
scripts/
  tts-capture.sh       # Claude Code Stop hook
claude-config/
  speak.md             # optional /speak slash command (alternative to hotkey)
install.sh
README.md
LICENSE
```

## Customize

- **Voice** - menu bar -> Voice. Ships with friendly names over Kokoro's A/B grade voices. Default is Scarlett (`af_sky`).
- **Speed** - menu bar -> Speed. 0.9x, 1.0x, 1.1x, 1.2x.
- **Auto-speak new responses** - menu toggle (off by default). When on, Claude's responses start speaking as soon as they finish; a newer response interrupts an older one.
- **Free memory when idle** - menu toggle (on by default). Unloads the Kokoro model after 15 minutes of inactivity, freeing ~1.3 GB; the next speak reloads it (a few seconds instead of ~200 ms). Tune with the `CLAUDIBLE_IDLE_UNLOAD_SECS` env var.
- **Different hotkeys** - not configurable yet; hardcoded to Cmd+Option+S / Cmd+Option+A in `app/main.py`.
- **Scripting** - `scripts/speak-toggle.sh` and `scripts/speak-selection.sh` send the same toggle signals over `/tmp/claudible.sock`, for wiring up skhd/BetterTouchTool/etc.

## Configuration

- **`~/.config/claudible/voices.json`** - overrides the default voice list. If missing or invalid, Claudible falls back to built-in voices. Reloads on app restart.
  - Requires a `default` string (a Kokoro voice id) and a `voices` array containing objects with `label` and `id` keys.
  ```json
  {
    "default": "af_sky",
    "voices": [
      { "label": "Scarlett", "id": "af_sky" },
      { "label": "Wren", "id": "af_alloy" }
    ]
  }
  ```
- **`~/.config/claudible/settings.json`** - automatically stores your last-used voice and speed from the menu bar. Users normally do not need to edit this file.
- **`CLAUDIBLE_CAPTURE_FILE`** (default `/tmp/claude-last-response.txt`) - the temporary file where the Claude Code Stop hook (`scripts/tts-capture.sh`) writes the assistant's last message.
- **`CLAUDIBLE_SOCKET`** (default `/tmp/claudible.sock`) - the Unix socket used for IPC. Both the stop hook and the manual toggle script (`scripts/speak-toggle.sh`) ping this socket. You would only need to change these environment variables for non-default install paths.
