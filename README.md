<p align="center">
  <img src="app/icon.png" alt="Claudible" width="160" />
</p>

# Claudible

A small macOS menu bar app that reads Claude Code's last response aloud with one global hotkey.

Claude + audible.

- 100% local. No API keys, no internet at runtime.
- Lives in your menu bar. Quits cleanly. Clears its cache on quit.
- Pre-synthesizes Claude's response while you're still reading the screen, so when you press the hotkey, audio starts in under 200 ms.

## Hotkey

**Cmd + Option + S** - speak Claude's last response. Press again to stop.

## Install

```bash
git clone https://github.com/blazemalan/claudible.git
cd claudible
./install.sh
```

Then open `Claudible.app` from `/Applications/`. First time you press the hotkey, macOS will ask for Accessibility permission for Claudible - grant it once.

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
- **Different hotkey** - not configurable yet; hardcoded to Cmd+Option+S in `app/main.py`.

