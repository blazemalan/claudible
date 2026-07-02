#!/usr/bin/env python3
"""Claudible: menu bar app that reads Claude Code's last response - or the text
you have highlighted in any app - aloud via Kokoro TTS.

Single-file design. Loads the Kokoro model on launch (and optionally unloads
it after an idle stretch, reloading on demand), registers global toggle
hotkeys (Cmd+Option+S = last response, Cmd+Option+A = current selection with
clipboard-contents fallback, via in-process pynput; requires macOS
Accessibility permission), and prefetches
Claude responses as they finish (prefetch/toggle/toggle-selection signals also
arrive on /tmp/claudible.sock).
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
# With "Free memory when idle" enabled, unload the model after this much
# inactivity (env override is mainly for testing).
IDLE_UNLOAD_SECS = int(os.environ.get("CLAUDIBLE_IDLE_UNLOAD_SECS", "900"))

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
    speakable_text,
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

SETTINGS = load_settings()

def notify(subtitle: str, message: str) -> None:
    """rumps.notification that can't take the app down - it raises in odd
    launch contexts (e.g. the binary run directly rather than via
    LaunchServices), and a failed toast must never kill a worker thread."""
    try:
        rumps.notification("Claudible", subtitle, message)
    except Exception as e:
        log(f"notification failed: {e} ({subtitle!r} {message!r})")


# ---------- Synthesis pipeline ----------


class Pipeline:
    def __init__(self):
        self.kokoro = None
        self.np = None
        self.voice = DEFAULT_VOICE
        self.speed = DEFAULT_SPEED
        self.current_proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.load_lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.is_playing = threading.Event()
        self.ready = threading.Event()
        self.last_used = time.time()
        self.on_play_state_change = None
        self.on_loading_change = None

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

    def ensure_loaded(self) -> bool:
        """Load the model if it isn't loaded (first launch or after an idle
        unload). Safe to call from any thread; concurrent callers wait for the
        one load. Returns False if loading failed."""
        if self.ready.is_set():
            return True
        with self.load_lock:
            if self.ready.is_set():
                return True
            if self.on_loading_change:
                self.on_loading_change(True)
            try:
                self.load_model()
                self.last_used = time.time()
                return True
            except Exception as e:
                log(f"model load failed: {e}")
                return False
            finally:
                if self.on_loading_change:
                    self.on_loading_change(False)

    def unload_model(self):
        """Drop the model to give its RAM back to the system. The next speak
        reloads it on demand (a few seconds instead of ~200 ms)."""
        with self.load_lock:
            if not self.ready.is_set() or self.is_playing.is_set():
                return
            self.ready.clear()
            self.kokoro = None
            import gc
            gc.collect()
            log("model unloaded after idle timeout; will reload on next use")

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
            if not self.ensure_loaded():
                return
            self.last_used = time.time()
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
            self.last_used = time.time()
            self.is_playing.clear()
            if self.on_play_state_change:
                self.on_play_state_change(False)
            log("play done")

    def prefetch_first(self, text: str):
        if not self.ready.is_set():
            log("prefetch skipped (model not loaded)")
            return
        self.last_used = time.time()
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
        self.settings = SETTINGS
        self.pipeline = Pipeline()
        self.pipeline.voice = self.settings["voice"]
        self.pipeline.speed = self.settings["speed"]
        self.pipeline.on_play_state_change = self._on_play_state_change
        self.pipeline.on_loading_change = self._on_loading_change
        log(f"restored settings: {self.settings}")
        self.speak_menu = rumps.MenuItem("Speak last  (Cmd+Option+S)", callback=self._toggle_speak_menu)
        self.selection_menu = rumps.MenuItem("Speak selection / clipboard  (Cmd+Option+A)", callback=self._toggle_selection_menu)
        self.auto_speak_menu = rumps.MenuItem("Auto-speak new responses", callback=self._toggle_auto_speak)
        self.auto_speak_menu.state = 1 if self.settings["auto_speak"] else 0
        self.idle_unload_menu = rumps.MenuItem("Free memory when idle", callback=self._toggle_idle_unload)
        self.idle_unload_menu.state = 1 if self.settings["idle_unload"] else 0
        self.menu = [
            self.speak_menu,
            self.selection_menu,
            None,
            self._build_voice_menu(),
            self._build_speed_menu(),
            self.auto_speak_menu,
            self.idle_unload_menu,
            None,
            rumps.MenuItem("Open log", callback=self._open_log),
            rumps.MenuItem("Clear cache", callback=self._clear_cache),
            None,
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]
        # Hotkeys are registered here on the main thread: pynput's setup asks
        # HIToolbox for the keyboard layout, which newer macOS asserts must
        # happen on the main queue (off-main it SIGTRAPs the process). Pressing
        # a hotkey before the model is ready just waits in ensure_loaded().
        self._start_hotkeys()
        threading.Thread(target=self._init_bg, daemon=True).start()

    def _on_play_state_change(self, is_playing: bool):
        self.title = " 🔊" if is_playing else ""
        self.speak_menu.title = "Stop speaking  (Cmd+Option+S)" if is_playing else "Speak last  (Cmd+Option+S)"
        self.selection_menu.title = "Stop speaking  (Cmd+Option+A)" if is_playing else "Speak selection / clipboard  (Cmd+Option+A)"

    def _on_loading_change(self, loading: bool):
        if loading:
            self.title = " loading..."
        else:
            self.title = " 🔊" if self.pipeline.is_playing.is_set() else ""

    def _init_bg(self):
        if not VOICES_CONFIG.exists():
            try:
                write_default_voices_config()
            except Exception as e:
                log(f"could not write default voices config: {e}")

        if not self.pipeline.ensure_loaded():
            self.title = " error"
            notify("Model load failed", "See /tmp/claudible.log")
            return
        notify("", "Ready  -  Cmd+Option+S last reply, Cmd+Option+A selection")
        self._start_socket_server()
        self._start_idle_watchdog()
        log("ready")

    def _build_voice_menu(self) -> rumps.MenuItem:
        m = rumps.MenuItem("Voice")
        for label, voice_id in VOICES:
            mi = rumps.MenuItem(label, callback=self._set_voice)
            mi._claudible_voice_id = voice_id
            if voice_id == self.settings["voice"]:
                mi.state = 1
            m.add(mi)
        return m

    def _build_speed_menu(self) -> rumps.MenuItem:
        m = rumps.MenuItem("Speed")
        for s in SPEEDS:
            mi = rumps.MenuItem(f"{s}x", callback=self._set_speed)
            if s == self.settings["speed"]:
                mi.state = 1
            m.add(mi)
        return m

    def _set_voice(self, sender):
        for label, _vid in VOICES:
            self.menu["Voice"][label].state = 0
        sender.state = 1
        self.pipeline.voice = getattr(sender, "_claudible_voice_id", sender.title)
        self.settings["voice"] = self.pipeline.voice
        save_settings(self.settings)

    def _set_speed(self, sender):
        for s in SPEEDS:
            self.menu["Speed"][f"{s}x"].state = 0
        sender.state = 1
        self.pipeline.speed = float(sender.title.rstrip("x"))
        self.settings["speed"] = self.pipeline.speed
        save_settings(self.settings)

    def _toggle_auto_speak(self, sender):
        sender.state = 0 if sender.state else 1
        self.settings["auto_speak"] = bool(sender.state)
        save_settings(self.settings)
        log(f"auto-speak {'enabled' if sender.state else 'disabled'}")

    def _toggle_idle_unload(self, sender):
        sender.state = 0 if sender.state else 1
        self.settings["idle_unload"] = bool(sender.state)
        save_settings(self.settings)
        log(f"idle unload {'enabled' if sender.state else 'disabled'}")

    def _start_idle_watchdog(self):
        def watch():
            while True:
                time.sleep(30)
                try:
                    if (
                        self.settings["idle_unload"]
                        and self.pipeline.ready.is_set()
                        and not self.pipeline.is_playing.is_set()
                        and time.time() - self.pipeline.last_used > IDLE_UNLOAD_SECS
                    ):
                        self.pipeline.unload_model()
                except Exception as e:
                    log(f"idle watchdog error: {e}")

        threading.Thread(target=watch, daemon=True).start()

    def _toggle_speak_menu(self, _):
        self._toggle_speak()

    def _toggle_selection_menu(self, _):
        self._toggle_speak_selection()

    def _open_log(self, _):
        subprocess.Popen(["open", str(LOG_FILE)])

    def _clear_cache(self, _):
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
        notify("Cache", "Cleared")

    def _on_quit(self, _):
        self._cleanup()
        rumps.quit_application()

    def _toggle_speak(self):
        if self.pipeline.is_playing.is_set():
            self.pipeline.stop()
            return
        if not CAPTURE_FILE.exists():
            notify("Nothing captured", "Wait for Claude's next response")
            return
        text = CAPTURE_FILE.read_text(encoding="utf-8", errors="replace")
        self.pipeline.play_text(text)

    # ---------- Speak selection ----------

    def _toggle_speak_selection(self):
        if self.pipeline.is_playing.is_set():
            self.pipeline.stop()
            return
        threading.Thread(target=self._speak_selection_worker, daemon=True).start()

    def _auto_speak_now(self, text: str):
        log(f"auto-speak: new response ({len(text)} chars)")
        self.pipeline.stop()
        for _ in range(40):  # let the previous playback wind down (~2 s max)
            if not self.pipeline.is_playing.is_set():
                break
            time.sleep(0.05)
        self.pipeline.play_text(text)

    def _speak_selection_worker(self):
        # Let the menu close / the hotkey's modifier keys come back up, so focus
        # is back on the user's app and held modifiers can't contaminate Cmd+C.
        time.sleep(0.25)
        trusted = self._accessibility_trusted()
        text = self._selection_via_ax()
        if text:
            log(f"selection: {len(text)} chars via accessibility API")
        elif trusted:
            text = self._selection_via_clipboard()
            if text:
                log(f"selection: {len(text)} chars via synthetic copy")
        else:
            log("selection: Accessibility permission missing; trying clipboard contents")
        if not text or not text.strip():
            # No readable selection - speak the clipboard instead (no permissions needed).
            text = self._clipboard_text()
            if text and text.strip():
                log(f"selection: {len(text)} chars via clipboard contents")
        if not text or not text.strip():
            if not trusted:
                notify("Accessibility permission needed", "Reading the selection needs Accessibility (System Settings > Privacy & Security); or copy text and try again.")
            else:
                notify("Nothing to read", "Highlight or copy some text, then try again")
            return
        self.pipeline.play_text(text)

    def _clipboard_text(self) -> str | None:
        """Plain-text clipboard contents; needs no permissions."""
        try:
            from AppKit import NSPasteboard, NSPasteboardTypeString
            return NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
        except Exception as e:
            log(f"selection: clipboard read failed: {e}")
            return None

    def _selection_via_ax(self) -> str | None:
        """Selected text of the focused UI element, read via the Accessibility
        API. No side effects, but some apps (Electron, Chrome) don't expose it."""
        try:
            from HIServices import (
                AXUIElementCopyAttributeValue,
                AXUIElementCreateSystemWide,
                kAXFocusedUIElementAttribute,
                kAXSelectedTextAttribute,
            )
            err, focused = AXUIElementCopyAttributeValue(
                AXUIElementCreateSystemWide(), kAXFocusedUIElementAttribute, None
            )
            if err or focused is None:
                return None
            err, selected = AXUIElementCopyAttributeValue(
                focused, kAXSelectedTextAttribute, None
            )
            if err or not selected:
                return None
            return str(selected)
        except Exception as e:
            log(f"selection: AX read failed: {e}")
            return None

    def _selection_via_clipboard(self) -> str | None:
        """Grab the selection by synthesizing Cmd+C in the frontmost app, then
        put the user's previous clipboard back. Requires Accessibility."""
        try:
            from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString
            from Quartz import (
                CGEventCreateKeyboardEvent,
                CGEventPost,
                CGEventSetFlags,
                kCGEventFlagMaskCommand,
                kCGHIDEventTap,
            )
        except Exception as e:
            log(f"selection: pyobjc unavailable for clipboard grab: {e}")
            return None

        pb = NSPasteboard.generalPasteboard()
        saved = []
        for item in pb.pasteboardItems() or []:
            types = {}
            for t in item.types() or []:
                data = item.dataForType_(t)
                if data is not None:
                    types[t] = data
            if types:
                saved.append(types)
        before = pb.changeCount()

        KEY_C = 8  # kVK_ANSI_C; explicit flags so held hotkey modifiers don't leak in
        for key_down in (True, False):
            ev = CGEventCreateKeyboardEvent(None, KEY_C, key_down)
            CGEventSetFlags(ev, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, ev)

        text = None
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if pb.changeCount() != before:
                text = pb.stringForType_(NSPasteboardTypeString)
                break
            time.sleep(0.05)
        else:
            log("selection: clipboard unchanged after Cmd+C (no selection?)")

        if pb.changeCount() != before:
            try:
                pb.clearContents()
                items = []
                for types in saved:
                    item = NSPasteboardItem.alloc().init()
                    for t, data in types.items():
                        item.setData_forType_(data, t)
                    items.append(item)
                if items:
                    pb.writeObjects_(items)
            except Exception as e:
                log(f"selection: clipboard restore failed: {e}")

        return str(text) if text else None

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
                            if self.settings["auto_speak"]:
                                # Newest response wins: interrupt whatever is
                                # playing and speak this one.
                                threading.Thread(
                                    target=self._auto_speak_now,
                                    args=(text,),
                                    daemon=True,
                                ).start()
                            else:
                                threading.Thread(
                                    target=self.pipeline.prefetch_first,
                                    args=(text,),
                                    daemon=True,
                                ).start()
                        elif head == "toggle":
                            self._toggle_speak()
                        elif head == "toggle-selection":
                            self._toggle_speak_selection()
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

        def on_speak_selection():
            self._toggle_speak_selection()

        try:
            listener = keyboard.GlobalHotKeys({
                "<cmd>+<alt>+s": on_speak,
                "<cmd>+<alt>+a": on_speak_selection,
            })
            listener.start()
        except Exception as e:
            log(f"hotkey listener failed: {e}")
            notify("Hotkeys disabled", "Grant Accessibility permission in System Settings")
            return

        if trusted:
            log("hotkeys active: Cmd+Option+S (last reply), Cmd+Option+A (selection)")
        else:
            log(
                "hotkey listener started, but Accessibility permission is NOT "
                "granted - Cmd+Option+S / Cmd+Option+A will do nothing until you "
                "allow Claudible in System Settings > Privacy & Security > "
                "Accessibility, then relaunch"
            )
            notify("Accessibility permission needed", "Allow Claudible under System Settings > Privacy & Security > Accessibility, then relaunch.")

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
