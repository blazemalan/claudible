from __future__ import annotations

import re
import time
from pathlib import Path

HOME = Path.home()
VOICES_CONFIG = HOME / ".config/claudible/voices.json"
SETTINGS_FILE = HOME / ".config/claudible/settings.json"
LOG_FILE = Path("/tmp/claudible.log")

DEFAULT_SPEED = 1.0

# Built-in defaults. Users can override entirely by writing
# ~/.config/claudible/voices.json (see README).
_BUILTIN_DEFAULT_VOICE = "af_sky"  # Scarlett
_BUILTIN_VOICES: list[tuple[str, str]] = [
    ("Ashley",   "af_heart"),
    ("Evie",     "af_bella"),
    ("Teri",     "af_nicole"),
    ("Bethanie", "af_sarah"),
    ("Scarlett", "af_sky"),
    ("Ivy",      "af_nova"),
    ("Wren",     "af_alloy"),
    ("Jessica",  "af_aoede"),
    ("Tatum",    "af_kore"),
    ("Kevin",    "am_fenrir"),
    ("Marc",     "am_michael"),
    ("Nick",     "am_puck"),
    ("Emma",     "bf_emma"),
    ("Keira",    "bf_isabella"),
    ("Joe",      "bm_fable"),
    ("Wilbur",   "bm_george"),
]

def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def load_voices_config() -> tuple[str, list[tuple[str, str]]]:
    """Read voice list + default from ~/.config/claudible/voices.json.
    Falls back to built-in defaults on missing file or parse error.
    """
    import json
    if not VOICES_CONFIG.exists():
        return _BUILTIN_DEFAULT_VOICE, _BUILTIN_VOICES
    try:
        data = json.loads(VOICES_CONFIG.read_text())
        voices = [(str(v["label"]), str(v["id"])) for v in data.get("voices", [])]
        if not voices:
            return _BUILTIN_DEFAULT_VOICE, _BUILTIN_VOICES
        default = str(data.get("default") or voices[0][1])
        return default, voices
    except Exception as e:
        log(f"voices.json parse error: {e}; using defaults")
        return _BUILTIN_DEFAULT_VOICE, _BUILTIN_VOICES

DEFAULT_VOICE, VOICES = load_voices_config()
SPEEDS = [0.9, 1.0, 1.1, 1.2]

DEFAULT_SETTINGS = {
    "voice": DEFAULT_VOICE,
    "speed": DEFAULT_SPEED,
    "auto_speak": False,   # speak responses as soon as Claude finishes them
    "idle_unload": True,   # free the model's RAM after a long idle stretch
}

def load_settings() -> dict:
    """Settings remembered from the last session, validated field-by-field
    against the current voice list / speed options. Any missing, corrupt, or
    no-longer-valid value falls back to its default (so files written by older
    versions keep working)."""
    import json
    settings = dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return settings
    if not isinstance(data, dict):
        return settings
    if data.get("voice") in {vid for _label, vid in VOICES}:
        settings["voice"] = data["voice"]
    try:
        if float(data.get("speed")) in SPEEDS:
            settings["speed"] = float(data["speed"])
    except (TypeError, ValueError):
        pass
    settings["auto_speak"] = bool(data.get("auto_speak", DEFAULT_SETTINGS["auto_speak"]))
    settings["idle_unload"] = bool(data.get("idle_unload", DEFAULT_SETTINGS["idle_unload"]))
    return settings

def save_settings(settings: dict) -> None:
    """Persist the current settings so the next launch restores them."""
    import json
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        log(f"could not save settings: {e}")

_md_renderer = None

def strip_markdown(text: str) -> str:
    global _md_renderer
    if _md_renderer is None:
        try:
            import mistune
            from mistune.plugins.formatting import strikethrough as strikethrough_plugin

            class PlainText(mistune.HTMLRenderer):
                def text(self, t): return t
                def emphasis(self, t): return t
                def strong(self, t): return t
                def codespan(self, t): return ""
                def block_code(self, c, info=None): return ""
                def link(self, t, url, title=None): return t or ""
                def image(self, alt, url, title=None): return alt or ""
                def heading(self, t, level, **a): return t + ". "
                def paragraph(self, t): return t + " "
                def list(self, t, ordered, **a): return t
                def list_item(self, t, **a): return t.strip() + ". "
                def thematic_break(self): return ""
                def block_quote(self, t): return t
                def linebreak(self): return " "
                def softbreak(self): return " "
                def block_html(self, h): return ""
                def inline_html(self, h): return ""
                def strikethrough(self, t): return t

            _md_renderer = mistune.create_markdown(
                renderer=PlainText(), plugins=[strikethrough_plugin]
            )
        except ImportError:
            _md_renderer = "regex"

    if _md_renderer == "regex":
        return _regex_strip(text)

    text = re.sub(r"https?://\S+", " ", text)
    out = _md_renderer(text)
    out = re.sub(r"`", "", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip()

def speakable_text(text: str) -> str:
    """Markdown-stripped text for TTS. If stripping deletes everything (e.g. the
    text is one big code block, which the renderer drops), fall back to the raw
    text with whitespace collapsed so there is always something to read."""
    out = strip_markdown(text)
    if not out:
        out = re.sub(r"\s+", " ", text).strip()
    return out

def _regex_strip(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def chunk_text(text: str, target_chars: int = 400) -> list[str]:
    """Splits text into chunks of sentences, up to target_chars length.
    Uses speakable_text so a pure-code input reads raw instead of going
    silent."""
    text = speakable_text(text)
    if not text:
        return []
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    chunks: list[str] = []
    cur = ""
    for s in sents:
        if not cur:
            cur = s
        elif len(cur) + 1 + len(s) <= target_chars:
            cur = cur + " " + s
        else:
            chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks
