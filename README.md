# Claudible

A small macOS menu bar app that reads Claude Code's last response aloud, in af_sky (or any Kokoro voice you pick), with global hotkeys.

Claude + audible.

- 100% local. No API keys, no internet at runtime.
- Lives in your menu bar. Quits cleanly. Clears its cache on quit.
- Pre-synthesizes Claude's response while you're still reading the screen, so when you press the hotkey, audio starts in under 200 ms.

## Hotkeys

- **Cmd + Shift + S** - speak Claude's last response. Press again to stop.
- **Cmd + Shift + H** - speak whatever you have highlighted (anywhere on your Mac, clipboard preserved).

## Install

```bash
git clone https://github.com/<you>/claudible.git
cd claudible
./install.sh
```

Then open `Claudible.app` from `/Applications/`. First time you press a hotkey, macOS will ask for Accessibility permission - grant it.

The installer:

- Downloads the Kokoro model (~340 MB) to `~/.local/share/kokoro-tts/`
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
You press Cmd+Shift+S
        |
        v
Audio starts ~100 ms later (cache hit), continues sentence-by-sentence
with each next chunk synthesized while the current one plays.
```

## Project layout

```
app/
  main.py              # the whole app: rumps menu bar + Kokoro pipeline + hotkeys
  setup.py             # py2app config
  requirements.txt
scripts/
  tts-capture.sh       # Claude Code Stop hook
claude-config/
  speak.md             # optional /speak slash command
install.sh
README.md
LICENSE
```

## Customize

- **Voice** - menu bar -> Voice. af_sky default; af_heart, af_bella, af_nova, am_adam, bf_emma, bm_george available.
- **Speed** - menu bar -> Speed. 0.9x, 1.0x, 1.1x, 1.2x.
- **More voices** - edit `VOICES` in `app/main.py` (kokoro-onnx ships 54).
- **Different hotkeys** - edit the `<cmd>+<shift>+s` strings in `_start_hotkeys` in `app/main.py` (pynput format).

## Why not Apple's Speak Selection (Option+Esc)?

Apple's built-in feature works great with Siri voices, but you have to manually select text first. Claudible is for the case where you just want the last assistant message read without selecting anything. Use both - they complement each other.
