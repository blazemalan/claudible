---
description: Tell claude-tts.app to pre-synth the latest response (hotkey is Cmd+Shift+S)
allowed-tools: [Bash]
---
!/usr/bin/python3 -c "import socket; s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(0.5); s.connect('/tmp/claude-tts.sock'); s.sendall(b'prefetch'); s.close()" 2>/dev/null || true

Reply with only the word: Prefetched.
