#!/bin/bash
# Copy current selection (preserving clipboard) and send it to Claudible.app
# to speak. Runs in skhd's process tree which has Accessibility permission.
SOCKET="/tmp/claudible.sock"
[ -S "$SOCKET" ] || exit 0

ORIG=$(pbpaste)
osascript -e 'tell application "System Events" to keystroke "c" using command down' 2>/dev/null
sleep 0.18
SEL=$(pbpaste)
printf %s "$ORIG" | pbcopy

if [ -z "$SEL" ] || [ "$SEL" = "$ORIG" ]; then
  exit 0
fi

# Send "play\n<bytes>" framing over the socket so the app can read variable-length text
/usr/bin/python3 - <<PYEOF
import socket, sys
text = """$(printf %s "$SEL" | sed 's/\\/\\\\/g; s/"/\\"/g')"""
data = ("play\n" + text).encode()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(0.5)
s.connect("$SOCKET")
s.sendall(data)
s.close()
PYEOF
