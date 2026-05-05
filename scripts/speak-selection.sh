#!/bin/bash
# Tell Claudible.app to copy the current selection (preserving clipboard) and speak it.
SOCKET="/tmp/claudible.sock"
[ -S "$SOCKET" ] || exit 0
/usr/bin/python3 -c "
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.settimeout(0.5)
    s.connect('$SOCKET')
    s.sendall(b'selection')
    s.close()
except Exception:
    pass
" 2>/dev/null
