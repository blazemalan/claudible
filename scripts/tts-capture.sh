#!/bin/bash
# Stop hook for Claude Code: write the last assistant message to a temp file
# and signal the Claudible app (if running) to pre-synthesize the first chunk.

CAPTURE_FILE="/tmp/claude-last-response.txt"
SOCKET="/tmp/claudible.sock"
MIN_LENGTH=50

sleep 1  # let transcript flush

input=$(cat)
transcript_path=$(echo "$input" | jq -r '.transcript_path')
transcript_path="${transcript_path/#\~/$HOME}"
[ ! -f "$transcript_path" ] && exit 0

seen_tool_result=0
claude_response=""
while IFS= read -r line; do
  message_type=$(echo "$line" | jq -r '.type' 2>/dev/null)
  if [ "$message_type" = "tool_result" ]; then
    seen_tool_result=1
  fi
  if [ "$message_type" = "assistant" ]; then
    TEXT=$(echo "$line" | jq -r '.message.content[]? | select(.type == "text") | .text' 2>/dev/null | tr '\n' ' ')
    if [ -n "$TEXT" ] && [ "$seen_tool_result" != "1" ]; then
      claude_response="$TEXT"
      break
    fi
  fi
done < <(tail -r "$transcript_path")

if [ ${#claude_response} -ge $MIN_LENGTH ]; then
  echo "$claude_response" > "$CAPTURE_FILE"
  # Ping the running Claudible app so it pre-synthesizes the first chunk
  if [ -S "$SOCKET" ]; then
    /usr/bin/python3 -c "
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.settimeout(0.5)
    s.connect('$SOCKET')
    s.sendall(b'prefetch')
    s.close()
except Exception:
    pass
" >/dev/null 2>&1 &
  fi
fi

exit 0
