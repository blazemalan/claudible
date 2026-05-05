# Claudible

A small macOS menu bar app that reads Claude Code's last response aloud, in af_sky (or any Kokoro voice you pick), with one global hotkey.

Claude + audible.

- 100% local. No API keys, no internet at runtime.
- Lives in your menu bar. Quits cleanly. Clears its cache on quit.
- Pre-synthesizes Claude's response while you're still reading the screen, so when you press the hotkey, audio starts in under 200 ms.

## Hotkey

**Cmd + Shift + S** - speak Claude's last response. Press again to stop.

(A "speak the highlighted text anywhere" hotkey is on the roadmap for v2 - macOS's permission model around third-party clipboard / selection access turned out to be messier than expected.)

## Install

```bash
git clone https://github.com/blazemalan/claudible.git
cd claudible
./install.sh
```

Then open `Claudible.app` from `/Applications/`. First time you press the hotkey, macOS will ask for Accessibility permission for skhd - grant it once.

The installer:

- Downloads the Kokoro model (~340 MB) to `~/.local/share/kokoro-tts/`
- Builds the `.app` bundle with `py2app`
- Copies it to `/Applications/Claudible.app`
- Installs `skhd` (a tiny hotkey daemon) and configures Cmd+Shift+S
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
[skhd] runs scripts/speak-toggle.sh, which sends "toggle" over the socket
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
  speak-toggle.sh      # what skhd runs on Cmd+Shift+S
skhd/
  skhdrc               # hotkey config template
claude-config/
  speak.md             # optional /speak slash command (alternative to hotkey)
install.sh
README.md
LICENSE
```

## Customize

- **Voice** - menu bar -> Voice. af_sky default; af_heart, af_bella, af_nova, am_adam, bf_emma, bm_george available.
- **Speed** - menu bar -> Speed. 0.9x, 1.0x, 1.1x, 1.2x.
- **More voices** - edit `VOICES` in `app/main.py` (kokoro-onnx ships 54).
- **Different hotkey** - edit `~/.config/skhd/skhdrc`, run `launchctl kickstart -k gui/$(id -u)/com.koekeishiya.skhd`.

## Read highlighted text instead?

Use macOS's built-in **Option+Esc** (Spoken Content -> Speak Selection in Accessibility settings). It uses your system Siri voice and works on any selected text. Pair Claudible (Cmd+Shift+S, last Claude response) with Option+Esc (manual selection) and you've got both bases covered.
