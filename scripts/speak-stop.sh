#!/bin/bash
# Stop any in-flight Kokoro playback. No replay.
curl -s --max-time 3 -X POST http://127.0.0.1:7891/stop >/dev/null 2>&1
exit 0
