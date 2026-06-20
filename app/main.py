#!/usr/bin/env python3
"""Claudible: menu bar app that reads Claude Code's last response aloud via Kokoro TTS.

Single-file design. Loads the Kokoro model on launch, registers a global toggle
hotkey (Cmd+Option+S, via in-process pynput; requires macOS Accessibility
permission), and prefetches Claude responses as they finish (prefetch/toggle
signals also arrive on /tmp/claudible.sock).
On Quit, clears /tmp/kokoro-cache.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import wave
from pathlib import Path

import rumps

HOME = Path.home()
MODEL = HOME / ".local/share/kokoro-tts/kokoro-v1.0.fp16.onnx"
VOICES_BIN = HOME / ".local/share/kokoro-tts/voices-v1.0.bin"
CACHE_DIR = Path("/tmp/kokoro-cache")
CAPTURE_FILE = Path("/tmp/claude-last-response.txt")
SOCKET_PATH = Path("/tmp/claudible.sock")
LOG_FILE = Path("/tmp/claudible.log")

TARGET_CHUNK_CHARS = 400

from claudible_core import (
    VOICES_CONFIG,
    SETTINGS_FILE,
    DEFAULT_SPEED,
    _BUILTIN_DEFAULT_VOICE,
    _BUILTIN_VOICES,
    DEFAULT_VOICE,
    VOICES,
    SPEEDS,
    load_voices_config,
    load_settings,
    save_settings,
    strip_markdown,
    _regex_strip,
    log,
    chunk_text,
)

def write_default_voices_config() -> None:
    """Seed ~/.config/claudible/voices.json from built-ins so the user has a
    starting point to edit."""
    import json
    VOICES_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    VOICES_CONFIG.write_text(
        json.dumps(
            {
                "_comment": "Customize Claudible's voice menu. 'label' shows in the menu, 'id' is the Kokoro voice id. Restart Claudible to reload.",
                "default": _BUILTIN_DEFAULT_VOICE,
                "voices": [{"label": l, "id": i} for l, i in _BUILTIN_VOICES],
            },
            indent=2,
        ) + "\n"
    )

INITIAL_VOICE, INITIAL_SPEED = load_settings()


# ---------- Synthesis pipeline ----------


class Pipeline:
    def __init__(self):
        self.kokoro = None
        self.np = None
        self.voice = DEFAULT_VOICE
        self.speed = DEFAULT_SPEED
        self.current_proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.is_playing = threading.Event()
        self.ready = threading.Event()
        self.on_play_state_change = None

    def load_model(self):
        import numpy
        self.np = numpy

        # Monkey-patch onnxruntime.InferenceSession so any code that loads ONNX
        # models (including kokoro-onnx, which doesn't expose `providers`) uses
        # the CoreML execution provider when available, falling back to CPU.
        coreml_active = False
        try:
            import onnxruntime as ort
            available = set(ort.get_available_providers())
            preferred = []
            if "CoreMLExecutionProvider" in available:
                preferred.append("CoreMLExecutionProvider")
            preferred.append("CPUExecutionProvider")
            orig_session = ort.InferenceSession

            def _patched_session(*args, **kwargs):
                # Always override providers so kokoro-onnx (which hard-codes
                # CPUExecutionProvider) gets CoreML when available.
                kwargs["providers"] = preferred
                return orig_session(*args, **kwargs)

            ort.InferenceSession = _patched_session
            coreml_active = "CoreMLExecutionProvider" in preferred
        except Exception as e:
            log(f"could not patch onnxruntime providers: {e}")

        from kokoro_onnx import Kokoro
        self.kokoro = Kokoro(str(MODEL), str(VOICES_BIN))
        # Verify which provider the loaded session actually uses
        try:
            sess = getattr(self.kokoro, "sess", None)
            providers_used = sess.get_providers() if sess else []
        except Exception:
            providers_used = []
        log(f"model loaded; providers={providers_used} (CoreML requested: {coreml_active})")
        self.ready.set()

    def split_chunks(self, text: str) -> list[str]:
        return chunk_text(text, TARGET_CHUNK_CHARS)

    def cache_path(self, sentence: str) -> Path:
        h = hashlib.sha256(
            f"{self.voice}:{self.speed}:{sentence}".encode()
        ).hexdigest()[:16]
        return CACHE_DIR / f"{h}.wav"

    def synth(self, sentence: str) -> Path | None:
        path = self.cache_path(sentence)
        if path.exists() and path.stat().st_size > 0:
            return path
        if not self.kokoro:
            return None
        try:
            samples, sr = self.kokoro.create(
                sentence, voice=self.voice, speed=self.speed, lang="en-us"
            )
        except Exception as e:
            log(f"synth error: {e}")
            return None
        if samples.dtype != self.np.int16:
            peak = float(self.np.max(self.np.abs(samples)) or 1.0)
            samples = (samples / peak * 32767.0).astype(self.np.int16)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(samples.tobytes())
        return path

    def kill_current(self):
        with self.lock:
            p = self.current_proc
            self.current_proc = None
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def stop(self):
        self.stop_flag.set()
        self.kill_current()

    def play_text(self, text: str):
        if not self.ready.is_set():
            log("model not ready")
            return
        threading.Thread(target=self._play_blocking, args=(text,), daemon=True).start()

    def _play_blocking(self, text: str):
        if self.is_playing.is_set():
            log("already playing, ignoring")
            return
        self.is_playing.set()
        if self.on_play_state_change:
            self.on_play_state_change(True)
        self.stop_flag.clear()
        try:
            chunks = self.split_chunks(text)
            if not chunks:
                return
            next_path = self.synth(chunks[0])
            for i, chunk in enumerate(chunks):
                if self.stop_flag.is_set():
                    break
                wav_path = next_path
                if not wav_path:
                    continue
                holder: dict[str, Path | None] = {"path": None}
                pt = None
                if i + 1 < len(chunks):
                    nxt = chunks[i + 1]

                    def prefetch():
                        holder["path"] = self.synth(nxt)

                    pt = threading.Thread(target=prefetch, daemon=True)
                    pt.start()
                self.kill_current()
                p = subprocess.Popen(
                    ["afplay", str(wav_path)],
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                with self.lock:
                    self.current_proc = p
                p.wait()
                if pt:
                    pt.join()
                    next_path = holder["path"]
                else:
                    next_path = None
        finally:
            self.is_playing.clear()
            if self.on_play_state_change:
                self.on_play_state_change(False)
            log("play done")

    def prefetch_first(self, text: str):
        if not self.ready.is_set():
            return
        chunks = self.split_chunks(text)
        if chunks:
            self.synth(chunks[0])
            log(f"prefetched first chunk ({len(chunks[0])} chars)")


# ---------- Menu bar app ----------


class App(rumps.App):
    def __init__(self):
        # Resolve the menu bar icon both in dev (alongside main.py) and in the
        # bundled .app (Resources/menubarTemplate.png).
        here = Path(__file__).resolve().parent
        icon_path = here / "menubarTemplate.png"
        if not icon_path.exists():
            icon_path = here.parent / "Resources" / "menubarTemplate.png"
        super().__init__(
            "Claudible",
            title=" loading...",
            icon=str(icon_path) if icon_path.exists() else None,
            template=True,
            quit_button=None,
        )
        self.pipeline = Pipeline()
        self.pipeline.voice = INITIAL_VOICE
        self.pipeline.speed = INITIAL_SPEED
        self.pipeline.on_play_state_change = self._on_play_state_change
        log(f"restored settings: voice={INITIAL_VOICE} speed={INITIAL_SPEED}")
        self.speak_menu = rumps.MenuItem("Speak last  (Cmd+Option+S)", callback=self._toggle_speak_menu)
        self.menu = [
            self.speak_menu,
            None,
            self._build_voice_menu(),
            self._build_speed_menu(),
            None,
            rumps.MenuItem("Open log", callback=self._open_log),
            rumps.MenuItem("Clear cache", callback=self._clear_cache),
            None,
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]
        threading.Thread(target=self._init_bg, daemon=True).start()

    def _on_play_state_change(self, is_playing: bool):
        self.title = " 🔊" if is_playing else ""
        self.speak_menu.title = "Stop speaking  (Cmd+Option+S)" if is_playing else "Speak last  (Cmd+Option+S)"

    def _init_bg(self):
        if not VOICES_CONFIG.exists():
            try:
                write_default_voices_config()
            except Exception as e:
                log(f"could not write default voices config: {e}")

        try:
            self.pipeline.load_model()
            self.title = ""
            rumps.notification("Claudible", "", "Ready  -  Cmd+Option+S to speak")
        except Exception as e:
            log(f"model load failed: {e}")
            self.title = " error"
            rumps.notification(
                "Claudible", "Model load failed", str(e)[:120]
            )
            return
        self._start_socket_server()
        self._start_hotkeys()
        log("ready")

    def _build_voice_menu(self) -> rumps.MenuItem:
        m = rumps.MenuItem("Voice")
        for label, voice_id in VOICES:
            mi = rumps.MenuItem(label, callback=self._set_voice)
            mi._claudible_voice_id = voice_id
            if voice_id == INITIAL_VOICE:
                mi.state = 1
            m.add(mi)
        return m

    def _build_speed_menu(self) -> rumps.MenuItem:
        m = rumps.MenuItem("Speed")
        for s in SPEEDS:
            mi = rumps.MenuItem(f"{s}x", callback=self._set_speed)
            if s == INITIAL_SPEED:
                mi.state = 1
            m.add(mi)
        return m

    def _set_voice(self, sender):
        for label, _vid in VOICES:
            self.menu["Voice"][label].state = 0
        sender.state = 1
        self.pipeline.voice = getattr(sender, "_claudible_voice_id", sender.title)
        save_settings(self.pipeline.voice, self.pipeline.speed)

    def _set_speed(self, sender):
        for s in SPEEDS:
            self.menu["Speed"][f"{s}x"].state = 0
        sender.state = 1
        self.pipeline.speed = float(sender.title.rstrip("x"))
        save_settings(self.pipeline.voice, self.pipeline.speed)

    def _toggle_speak_menu(self, _):
        self._toggle_speak()

    def _open_log(self, _):
        subprocess.Popen(["open", str(LOG_FILE)])

    def _clear_cache(self, _):
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
        rumps.notification("Claudible", "Cache", "Cleared")

    def _on_quit(self, _):
        self._cleanup()
        rumps.quit_application()

    def _toggle_speak(self):
        if self.pipeline.is_playing.is_set():
            self.pipeline.stop()
            return
        if not CAPTURE_FILE.exists():
            rumps.notification(
                "Claudible", "Nothing captured", "Wait for Claude's next response"
            )
            return
        text = CAPTURE_FILE.read_text(encoding="utf-8", errors="replace")
        self.pipeline.play_text(text)

    def _start_socket_server(self):
        def serve():
            try:
                if SOCKET_PATH.exists():
                    SOCKET_PATH.unlink()
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                srv.bind(str(SOCKET_PATH))
                srv.listen(5)
            except Exception as e:
                log(f"socket server bind error: {e}")
                return
            while True:
                try:
                    conn, _ = srv.accept()
                    try:
                        chunks = []
                        while True:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            chunks.append(chunk)
                            if sum(len(c) for c in chunks) > 1024 * 1024:
                                break
                        data = b"".join(chunks).decode("utf-8", errors="replace")
                        head, _, body = data.partition("\n")
                        head = head.strip()
                        if head == "prefetch" and CAPTURE_FILE.exists():
                            text = CAPTURE_FILE.read_text(encoding="utf-8", errors="replace")
                            threading.Thread(
                                target=self.pipeline.prefetch_first,
                                args=(text,),
                                daemon=True,
                            ).start()
                        elif head == "toggle":
                            self._toggle_speak()
                    finally:
                        conn.close()
                except Exception as e:
                    import traceback
                    log(f"socket server error: {e}\n{traceback.format_exc()}")

        threading.Thread(target=serve, daemon=True).start()

    def _accessibility_trusted(self, prompt: bool = False) -> bool:
        """True if Claudible has macOS Accessibility permission (required for the
        global hotkey). With prompt=True, also asks macOS to show its "allow
        control" dialog and add Claudible to the Accessibility list. Returns True
        if the check itself is unavailable, so we never block startup."""
        try:
            from HIServices import (
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )
            return bool(
                AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)})
            )
        except Exception as e:
            log(f"accessibility check unavailable: {e}")
            return True

    def _start_hotkeys(self):
        try:
            from pynput import keyboard
        except ImportError:
            log("pynput not installed; hotkeys disabled")
            return

        # The global hotkey needs macOS Accessibility permission. pynput's
        # listener "starts" whether or not it's granted, so check explicitly and
        # prompt the user rather than silently doing nothing.
        trusted = self._accessibility_trusted(prompt=True)

        def on_speak():
            self._toggle_speak()

        try:
            listener = keyboard.GlobalHotKeys({
                "<cmd>+<alt>+s": on_speak,
            })
            listener.start()
        except Exception as e:
            log(f"hotkey listener failed: {e}")
            rumps.notification(
                "Claudible",
                "Hotkeys disabled",
                "Grant Accessibility permission in System Settings",
            )
            return

        if trusted:
            log("hotkeys active: Cmd+Option+S")
        else:
            log(
                "hotkey listener started, but Accessibility permission is NOT "
                "granted - Cmd+Option+S will do nothing until you allow Claudible "
                "in System Settings > Privacy & Security > Accessibility, then relaunch"
            )
            rumps.notification(
                "Claudible",
                "Accessibility permission needed",
                "Allow Claudible under System Settings > Privacy & Security > Accessibility, then relaunch.",
            )

    def _cleanup(self):
        try:
            self.pipeline.stop()
        except Exception:
            pass
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
        try:
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()
        except Exception:
            pass
        log("clean exit")


if __name__ == "__main__":
    App().run()
