#!/usr/bin/env python3
"""Long-running Kokoro TTS daemon.

POST /play   body: {"text": "...", "voice": "af_sky"}    -> 202 Accepted, audio plays via afplay
POST /stop                                                -> kills in-flight afplay + drains queue
GET  /health                                              -> {"ok": true}
"""
import hashlib
import io
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
from kokoro_onnx import Kokoro

MODEL = "/Users/bmalan/.local/share/kokoro-tts/kokoro-v1.0.onnx"
VOICES = "/Users/bmalan/.local/share/kokoro-tts/voices-v1.0.bin"
PORT = 7891

print("[kokoro-server] Loading model...", flush=True)
kokoro = Kokoro(MODEL, VOICES)
print("[kokoro-server] Model loaded.", flush=True)


def log(msg: str) -> None:
    sys.stderr.write(f"[kokoro-server] {msg}\n")
    sys.stderr.flush()


def samples_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    if samples.dtype != np.int16:
        peak = float(np.max(np.abs(samples)) or 1.0)
        samples = (samples / peak * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def strip_for_tts(text: str) -> str:
    """Strip markdown / URLs / paths so Kokoro doesn't read 'asterisk asterisk'."""
    # Strip code fences and inline code (entire content, since reading code aloud is noise)
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`[^`]*`", " ", text)
    # Strip bare URLs
    text = re.sub(r"https?://\S+", " a link ", text)
    # Markdown links / images: [text](url) -> text, ![alt](url) -> alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Headings: drop leading hashes
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold / italic / strikethrough markers (keep content)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    # Bullet markers at start of lines
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Bare unix paths and tilde paths
    text = re.sub(r"(?:^|\s)~/[^\s]+", " ", text)
    text = re.sub(r"(?:^|\s)/[A-Za-z0-9_./-]+", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    text = strip_for_tts(text)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


play_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
current_afplay_lock = threading.Lock()
current_afplay: subprocess.Popen | None = None
stop_requested = threading.Event()


def kill_current_afplay() -> None:
    global current_afplay
    with current_afplay_lock:
        p = current_afplay
        current_afplay = None
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


CACHE_DIR = "/tmp/kokoro-cache"
os.makedirs(CACHE_DIR, exist_ok=True)


def cache_path(sentence: str, voice: str) -> str:
    h = hashlib.sha256(f"{voice}:{sentence}".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{h}.wav")


def synth_to_wav_path(sentence: str, voice: str) -> str | None:
    path = cache_path(sentence, voice)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        log(f"cache hit: {path}")
        return path
    try:
        samples, sr = kokoro.create(sentence, voice=voice, speed=1.0, lang="en-us")
    except Exception as e:
        log(f"synth error: {e}")
        return None
    wav = samples_to_wav_bytes(samples, sr)
    with open(path, "wb") as f:
        f.write(wav)
    return path


def worker() -> None:
    global current_afplay
    while True:
        text, voice = play_queue.get()
        log(f"play request: {len(text)} chars, voice={voice}")
        stop_requested.clear()
        sentences = split_sentences(text)
        if not sentences:
            continue

        # Synthesize first sentence before starting playback
        next_path = synth_to_wav_path(sentences[0], voice)
        prefetch_thread: threading.Thread | None = None
        prefetch_holder: dict[str, str | None] = {"path": None}

        def make_prefetch(s: str, v: str, holder: dict) -> threading.Thread:
            def run():
                holder["path"] = synth_to_wav_path(s, v)
            t = threading.Thread(target=run, daemon=True)
            t.start()
            return t

        for i in range(len(sentences)):
            if stop_requested.is_set():
                log("stop requested, dropping remainder")
                break
            wav_path = next_path
            if not wav_path:
                continue

            # Kick off prefetch of the next sentence in parallel with current playback
            if i + 1 < len(sentences):
                prefetch_holder = {"path": None}
                prefetch_thread = make_prefetch(sentences[i + 1], voice, prefetch_holder)
            else:
                prefetch_thread = None

            log(f"playing sentence {i} ({len(sentences[i])} chars)")
            kill_current_afplay()
            p = subprocess.Popen(
                ["afplay", wav_path],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            with current_afplay_lock:
                current_afplay = p
            p.wait()

            # Wait for prefetch to finish (usually already done while audio was playing)
            if prefetch_thread:
                prefetch_thread.join()
                next_path = prefetch_holder["path"]
            else:
                next_path = None
        log("play request done")


threading.Thread(target=worker, daemon=True).start()


class H(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode()) if n else {}
        except Exception as e:
            self._json(400, {"error": f"bad json: {e}"})
            return

        if self.path == "/play":
            text = (data.get("text") or "").strip()
            voice = data.get("voice") or "af_sky"
            if not text:
                self._json(400, {"error": "text required"})
                return
            stop_requested.set()
            kill_current_afplay()
            try:
                while True:
                    play_queue.get_nowait()
            except queue.Empty:
                pass
            play_queue.put((text, voice))
            self._json(202, {"ok": True, "queued": len(text)})
        elif self.path == "/stop":
            stop_requested.set()
            kill_current_afplay()
            try:
                while True:
                    play_queue.get_nowait()
            except queue.Empty:
                pass
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("[kokoro-server] " + (fmt % args) + "\n")


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), H)
    log(f"Listening on 127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
