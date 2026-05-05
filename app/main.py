#!/usr/bin/env python3
"""Claudible: menu bar app that reads Claude Code's last response aloud via Kokoro TTS.

Single-file design. Loads the Kokoro model on launch, accepts a global toggle
hotkey (Cmd+Shift+S, delivered via skhd through /tmp/claudible.sock), and
prefetches Claude responses as they finish.
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
MODEL = HOME / ".local/share/kokoro-tts/kokoro-v1.0.onnx"
VOICES_BIN = HOME / ".local/share/kokoro-tts/voices-v1.0.bin"
CACHE_DIR = Path("/tmp/kokoro-cache")
CAPTURE_FILE = Path("/tmp/claude-last-response.txt")
SOCKET_PATH = Path("/tmp/claudible.sock")
LOG_FILE = Path("/tmp/claudible.log")

DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.0
TARGET_CHUNK_CHARS = 400
VOICES = [
    # American female
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky", "af_nova",
    "af_alloy", "af_aoede", "af_jessica", "af_kore", "af_river",
    # American male
    "am_adam", "am_echo", "am_eric", "am_liam", "am_michael", "am_onyx",
    "am_fenrir", "am_puck", "am_santa",
    # British female
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    # British male
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]
SPEEDS = [0.9, 1.0, 1.1, 1.2]


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ---------- Markdown stripping (mistune if available, regex fallback) ----------

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

    def load_model(self):
        from kokoro_onnx import Kokoro
        import numpy

        self.np = numpy
        self.kokoro = Kokoro(str(MODEL), str(VOICES_BIN))
        self.ready.set()
        log("model loaded")

    def split_chunks(self, text: str) -> list[str]:
        text = strip_markdown(text)
        if not text:
            return []
        sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        chunks: list[str] = []
        cur = ""
        for s in sents:
            if not cur:
                cur = s
            elif len(cur) + 1 + len(s) <= TARGET_CHUNK_CHARS:
                cur = cur + " " + s
            else:
                chunks.append(cur)
                cur = s
        if cur:
            chunks.append(cur)
        return chunks

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
        self.menu = [
            rumps.MenuItem("Speak last  (Cmd+Option+S)", callback=self._toggle_speak_menu),
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

    def _init_bg(self):
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
        for v in VOICES:
            mi = rumps.MenuItem(v, callback=self._set_voice)
            if v == DEFAULT_VOICE:
                mi.state = 1
            m.add(mi)
        return m

    def _build_speed_menu(self) -> rumps.MenuItem:
        m = rumps.MenuItem("Speed")
        for s in SPEEDS:
            mi = rumps.MenuItem(f"{s}x", callback=self._set_speed)
            if s == DEFAULT_SPEED:
                mi.state = 1
            m.add(mi)
        return m

    def _set_voice(self, sender):
        for v in VOICES:
            self.menu["Voice"][v].state = 0
        sender.state = 1
        self.pipeline.voice = sender.title

    def _set_speed(self, sender):
        for s in SPEEDS:
            self.menu["Speed"][f"{s}x"].state = 0
        sender.state = 1
        self.pipeline.speed = float(sender.title.rstrip("x"))

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
        text = CAPTURE_FILE.read_text()
        self.pipeline.play_text(text)

    def _start_socket_server(self):
        def serve():
            try:
                if SOCKET_PATH.exists():
                    SOCKET_PATH.unlink()
                srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                srv.bind(str(SOCKET_PATH))
                srv.listen(5)
                while True:
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
                            text = CAPTURE_FILE.read_text()
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
                log(f"socket server error: {e}")

        threading.Thread(target=serve, daemon=True).start()

    def _start_hotkeys(self):
        try:
            from pynput import keyboard
        except ImportError:
            log("pynput not installed; hotkeys disabled")
            return

        def on_speak():
            self._toggle_speak()

        def on_select():
            self._do_selection()

        try:
            listener = keyboard.GlobalHotKeys({
                "<cmd>+<shift>+s": on_speak,
                "<cmd>+<shift>+h": on_select,
            })
            listener.start()
            log("hotkeys active: Cmd+Shift+S, Cmd+Shift+H")
        except Exception as e:
            log(f"hotkey listener failed: {e}")
            rumps.notification(
                "Claudible",
                "Hotkeys disabled",
                "Grant Accessibility permission in System Settings",
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
