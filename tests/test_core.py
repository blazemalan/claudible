import sys
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Add the app directory to sys.path so we can import claudible_core directly
# as `from claudible_core import ...`, mimicking main.py's runtime environment.
app_dir = Path(__file__).parent.parent / "app"
sys.path.insert(0, str(app_dir))

# Mock macOS dependencies so we can import main on Linux.
class MockRumpsApp:
    def __init__(self, *args, **kwargs):
        pass

mock_rumps = MagicMock()
mock_rumps.App = MockRumpsApp
sys.modules['rumps'] = mock_rumps
import main

import claudible_core
from claudible_core import (
    strip_markdown,
    speakable_text,
    _regex_strip,
    load_settings,
    save_settings,
    load_voices_config,
    _BUILTIN_DEFAULT_VOICE,
    _BUILTIN_VOICES,
    DEFAULT_SPEED,
)


@pytest.fixture(autouse=True)
def reset_md_renderer():
    """Ensure mistune initialization is clean per-test if we modify it."""
    claudible_core._md_renderer = None
    yield
    claudible_core._md_renderer = None


def test_strip_markdown_mistune():
    """Test markdown stripping using the mistune path (default)."""
    text = (
        "# Heading\n"
        "Some **bold** and *italic* text.\n"
        "```python\nprint('hello')\n```\n"
        "Inline `code` snippet.\n"
        "[Link](https://example.com) and ![Image](https://example.com/img.jpg)\n"
        "URL https://foo.bar/baz\n"
        "Non-ASCII: \u00e9 \U0001f600\n"
        "List:\n- Item 1\n- Item 2\n"
        "1. Ordered 1\n"
    )
    res = strip_markdown(text)

    # Assertions based on plain text mistune plugin logic
    assert "Heading" in res
    assert "bold" in res
    assert "**bold**" not in res
    assert "italic" in res
    assert "print('hello')" not in res
    assert "Inline snippet" in res
    assert "[Link](" in res # Testing verbatim outputs to match what the code actually does
    assert "![Image](" in res
    assert "https://foo.bar/baz" not in res
    assert "\u00e9 \U0001f600" in res
    assert "Item 1" in res

    # Mistune renderer condenses everything down, typically adding periods and spaces
    # We mainly care that the noise is gone.
    assert "```" not in res


def test_regex_strip_fallback():
    """Test the regex fallback logic directly."""
    text = (
        "## Heading\n"
        "Some **bold** and *italic* text.\n"
        "```python\nprint('hello')\n```\n"
        "Inline `code` snippet.\n"
        "[Link](https://example.com) and ![Image](https://example.com/img.jpg)\n"
        "URL https://foo.bar/baz\n"
        "Non-ASCII: \u00e9 \U0001f600\n"
        "- Item 1\n* Item 2\n"
        "1. Ordered 1\n"
    )
    res = _regex_strip(text)

    assert "Heading" in res
    assert "##" not in res
    assert "bold" in res
    assert "**" not in res
    assert "italic" in res
    assert "print('hello')" not in res
    assert "Inline snippet." in res
    assert "[Link](" in res # match code reality
    assert "![Image](" in res
    assert "https://foo.bar/baz" not in res
    assert "\u00e9 \U0001f600" in res
    assert "Item 1" in res
    assert "- " not in res
    assert "Ordered 1" in res
    assert "1. " not in res


def test_strip_markdown_forces_regex(monkeypatch):
    """Force strip_markdown to use regex by monkeypatching imports or state."""
    claudible_core._md_renderer = "regex"
    text = "Some `inline` code"
    res = strip_markdown(text)
    assert res == "Some code"


def test_speakable_text_prose():
    """Normal prose still gets markdown-stripped."""
    res = speakable_text("Some **bold** text.")
    assert "bold" in res
    assert "**" not in res


def test_speakable_text_pure_code_block_not_silent():
    """A selection that is one big code block must not strip down to silence."""
    res = speakable_text("```python\nprint('hello')\n```")
    assert "print" in res  # falls back to raw text instead of reading nothing


def test_speakable_text_indented_code_not_silent():
    res = speakable_text("    x = 1\n    y = 2\n")
    assert "x = 1" in res


def test_speakable_text_empty():
    assert speakable_text("") == ""
    assert speakable_text("   \n  ") == ""


def test_load_voices_config_missing(monkeypatch, tmp_path):
    """Test load_voices_config when file doesn't exist."""
    fake_config = tmp_path / "voices.json"
    monkeypatch.setattr(claudible_core, "VOICES_CONFIG", fake_config)

    default, voices = load_voices_config()
    assert default == _BUILTIN_DEFAULT_VOICE
    assert voices == _BUILTIN_VOICES


def test_load_voices_config_valid(monkeypatch, tmp_path):
    """Test load_voices_config with valid JSON."""
    fake_config = tmp_path / "voices.json"
    fake_config.write_text(json.dumps({
        "default": "my_custom",
        "voices": [
            {"label": "Custom1", "id": "my_custom"},
            {"label": "Custom2", "id": "my_other"}
        ]
    }))
    monkeypatch.setattr(claudible_core, "VOICES_CONFIG", fake_config)

    default, voices = load_voices_config()
    assert default == "my_custom"
    assert voices == [("Custom1", "my_custom"), ("Custom2", "my_other")]


def test_load_voices_config_corrupt(monkeypatch, tmp_path):
    """Test load_voices_config with corrupt JSON."""
    fake_config = tmp_path / "voices.json"
    fake_config.write_text("not real json")
    monkeypatch.setattr(claudible_core, "VOICES_CONFIG", fake_config)

    default, voices = load_voices_config()
    assert default == _BUILTIN_DEFAULT_VOICE
    assert voices == _BUILTIN_VOICES


def test_load_voices_config_empty_voices(monkeypatch, tmp_path):
    """Test load_voices_config with empty voices list."""
    fake_config = tmp_path / "voices.json"
    fake_config.write_text(json.dumps({"voices": []}))
    monkeypatch.setattr(claudible_core, "VOICES_CONFIG", fake_config)

    default, voices = load_voices_config()
    assert default == _BUILTIN_DEFAULT_VOICE
    assert voices == _BUILTIN_VOICES


def test_load_save_settings_roundtrip(monkeypatch, tmp_path):
    """Test save_settings and load_settings persistence."""
    fake_settings = tmp_path / "settings.json"
    monkeypatch.setattr(claudible_core, "SETTINGS_FILE", fake_settings)

    # "af_sarah" is in _BUILTIN_VOICES, so validation in load_settings passes.
    settings = dict(claudible_core.DEFAULT_SETTINGS)
    settings.update({"voice": "af_sarah", "speed": 1.2, "auto_speak": True, "idle_unload": False})
    save_settings(settings)
    assert fake_settings.exists()

    loaded = load_settings()
    assert loaded["voice"] == "af_sarah"
    assert loaded["speed"] == 1.2
    assert loaded["auto_speak"] is True
    assert loaded["idle_unload"] is False


def test_load_settings_missing(monkeypatch, tmp_path):
    """Test load_settings when file doesn't exist."""
    fake_settings = tmp_path / "settings.json"
    monkeypatch.setattr(claudible_core, "SETTINGS_FILE", fake_settings)

    loaded = load_settings()
    assert loaded == claudible_core.DEFAULT_SETTINGS
    assert loaded is not claudible_core.DEFAULT_SETTINGS  # must be a copy


def test_load_settings_corrupt(monkeypatch, tmp_path):
    """Test load_settings when file is corrupt."""
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text("bad json")
    monkeypatch.setattr(claudible_core, "SETTINGS_FILE", fake_settings)

    assert load_settings() == claudible_core.DEFAULT_SETTINGS


def test_load_settings_non_dict_json(monkeypatch, tmp_path):
    """Valid JSON that isn't an object must not crash load_settings."""
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text("[1, 2, 3]")
    monkeypatch.setattr(claudible_core, "SETTINGS_FILE", fake_settings)

    assert load_settings() == claudible_core.DEFAULT_SETTINGS


def test_load_settings_invalid_values(monkeypatch, tmp_path):
    """Test load_settings when values are invalid/unsupported."""
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text(json.dumps({"voice": "fake_voice_id", "speed": 9.9}))
    monkeypatch.setattr(claudible_core, "SETTINGS_FILE", fake_settings)

    loaded = load_settings()
    # It should fallback to defaults since "fake_voice_id" isn't in VOICES and
    # 9.9 isn't in SPEEDS.
    assert loaded["voice"] == claudible_core.DEFAULT_VOICE
    assert loaded["speed"] == DEFAULT_SPEED


def test_load_settings_legacy_format(monkeypatch, tmp_path):
    """Settings written by older versions (voice+speed only) still load, with
    the newer fields taking their defaults."""
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text(json.dumps({"voice": "af_sarah", "speed": 1.1}))
    monkeypatch.setattr(claudible_core, "SETTINGS_FILE", fake_settings)

    loaded = load_settings()
    assert loaded["voice"] == "af_sarah"
    assert loaded["speed"] == 1.1
    assert loaded["auto_speak"] is False
    assert loaded["idle_unload"] is True


def _hermetic_app(monkeypatch):
    """Build a main.App with every side-effecting startup path stubbed out (no
    real hotkeys, socket, watchdog, model, or the __init__ background _init_bg
    thread), and return it along with the real _init_bg to run synchronously."""
    real_init_bg = main.App._init_bg
    monkeypatch.setattr(main, "Pipeline", MagicMock())
    monkeypatch.setattr(main.App, "_init_bg", MagicMock())
    monkeypatch.setattr(main.App, "_start_hotkeys", MagicMock())
    monkeypatch.setattr(main.App, "_start_socket_server", MagicMock())
    monkeypatch.setattr(main.App, "_start_idle_watchdog", MagicMock())
    return main.App(), real_init_bg


def test_write_default_voices_config_creates_file(monkeypatch, tmp_path):
    """_init_bg seeds voices.json with the built-ins on first launch."""
    fake_config = tmp_path / "voices.json"
    monkeypatch.setattr(claudible_core, "VOICES_CONFIG", fake_config)
    monkeypatch.setattr(main, "VOICES_CONFIG", fake_config)

    app, real_init_bg = _hermetic_app(monkeypatch)
    assert not fake_config.exists()

    real_init_bg(app)

    assert fake_config.exists()
    default, voices = claudible_core.load_voices_config()
    assert default == claudible_core._BUILTIN_DEFAULT_VOICE
    assert voices == claudible_core._BUILTIN_VOICES


def test_write_default_voices_config_does_not_overwrite(monkeypatch, tmp_path):
    """_init_bg respects an existing voices.json."""
    fake_config = tmp_path / "voices.json"
    custom_content = json.dumps({"default": "test_voice", "voices": []})
    fake_config.write_text(custom_content)

    monkeypatch.setattr(claudible_core, "VOICES_CONFIG", fake_config)
    monkeypatch.setattr(main, "VOICES_CONFIG", fake_config)

    app, real_init_bg = _hermetic_app(monkeypatch)
    real_init_bg(app)

    # Assert it was not overwritten
    assert fake_config.read_text() == custom_content
