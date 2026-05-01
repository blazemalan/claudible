#!/bin/bash
# Fire-and-forget POST to the kokoro server's /play endpoint.
# Server handles synthesis + playback asynchronously via worker thread.

CAPTURE_FILE="/tmp/claude-last-response.txt"
KOKORO_VOICE="${KOKORO_VOICE:-af_sky}"
SERVER="http://127.0.0.1:7891"

if [ ! -f "$CAPTURE_FILE" ]; then
  echo "Nothing captured yet."
  exit 0
fi

PAYLOAD=$(jq -n --arg t "$(cat "$CAPTURE_FILE")" --arg v "$KOKORO_VOICE" '{text: $t, voice: $v}')
curl -s --max-time 5 -X POST "$SERVER/play" \
  -H "Content-Type: application/json" -d "$PAYLOAD" >/dev/null 2>&1 \
  && echo "Speaking..." \
  || echo "Kokoro server unreachable."
exit 0
