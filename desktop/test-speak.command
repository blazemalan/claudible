#!/bin/bash
# Standalone /speak test. Run from Terminal:
#   bash ~/Desktop/test-speak.command
# Or double-click. Times each step so we can see where the latency is.

set -e

cd "$(dirname "$0")"

TEXT="${1:-Poop poop poop poop. This is a second sentence to test sentence chunking. And here is a third sentence so we can measure timing across multiple chunks.}"
SERVER="http://127.0.0.1:7891"
WAV="/tmp/test-speak.wav"

echo
echo "================================================================"
echo "  /speak standalone test"
echo "================================================================"
echo "Text length: ${#TEXT} chars"
echo "Server: $SERVER"
echo

# 1. Health check
T0=$(date +%s.%N)
HEALTH=$(curl -s --max-time 3 "$SERVER/health")
T1=$(date +%s.%N)
echo "[1] /health: $HEALTH  (took $(echo "$T1 - $T0" | bc)s)"

# 2. Synthesize entire response (returns WAV bytes via the legacy /speak endpoint
#    that the new server doesn't have anymore -- so use /play and let server play)
echo
echo "Test A: server-side playback via POST /play (afplay spawned by launchd server)"
echo "  if you hear nothing here, the launchd-context-afplay theory is confirmed."
T0=$(date +%s.%N)
RESP=$(curl -s --max-time 5 -X POST "$SERVER/play" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg t "$TEXT" --arg v "af_sky" '{text: $t, voice: $v}')")
T1=$(date +%s.%N)
echo "[2] POST /play: $RESP  (took $(echo "$T1 - $T0" | bc)s)"
echo "    listen now for ~10 seconds. press Enter when done (heard or silent)."
read -r _

# 3. Now stop server playback and try a CLIENT-side path:
#    server already lacks a /speak endpoint that returns WAV, so we'll use a
#    separate uv run with kokoro_onnx to synthesize fresh, then afplay locally.
echo
echo "Test B: client-side afplay (audio generated separately, played from this Terminal)"
echo "  if THIS is audible but Test A was silent, fix is to switch /speak to client-side playback."
echo "  generating audio... (CPU synthesis, may take a few seconds)"

T0=$(date +%s.%N)
/opt/homebrew/bin/uv tool run --from kokoro-onnx python3 - <<PYEOF
import wave, numpy as np
from kokoro_onnx import Kokoro
k = Kokoro("/Users/bmalan/.local/share/kokoro-tts/kokoro-v1.0.onnx",
           "/Users/bmalan/.local/share/kokoro-tts/voices-v1.0.bin")
samples, sr = k.create("""$TEXT""", voice="af_sky", speed=1.0, lang="en-us")
if samples.dtype != np.int16:
    peak = float(np.max(np.abs(samples)) or 1.0)
    samples = (samples / peak * 32767.0).astype(np.int16)
with wave.open("$WAV", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(samples.tobytes())
PYEOF
T1=$(date +%s.%N)
echo "[3] synth: took $(echo "$T1 - $T0" | bc)s, wav=$(stat -f%z "$WAV") bytes"

T0=$(date +%s.%N)
afplay "$WAV"
T1=$(date +%s.%N)
echo "[4] afplay (client-side): took $(echo "$T1 - $T0" | bc)s"

echo
echo "================================================================"
echo "  done. press Enter to close."
echo "================================================================"
read -r _
