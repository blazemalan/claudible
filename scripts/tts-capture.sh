#!/bin/bash
# Silently capture Claude's last substantial response to /tmp/claude-last-response.txt
# Does NOT play audio. Use /speak to play.

CAPTURE_FILE="/tmp/claude-last-response.txt"
MIN_LENGTH=50

# Wait for the transcript file to fully flush (race condition fix)
sleep 1

input=$(cat)
transcript_path=$(echo "$input" | jq -r '.transcript_path')
transcript_path="${transcript_path/#\~/$HOME}"

[ ! -f "$transcript_path" ] && exit 0

# Walk transcript backwards, find latest assistant text that came AFTER any tool_result
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

# Skip short responses (e.g. "Speaking." from /speak itself) so big response stays cached
if [ ${#claude_response} -ge $MIN_LENGTH ]; then
  echo "$claude_response" > "$CAPTURE_FILE"
fi

exit 0
