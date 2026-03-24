#!/bin/bash
echo "=== macOS Version ==="
sw_vers

echo ""
echo "=== Mail Database ==="
MAIL_DB=$(find ~/Library/Mail -name "Envelope Index" -print 2>/dev/null | head -1)
if [ -z "$MAIL_DB" ]; then
    echo "⛔ NO ENVELOPE INDEX FOUND — BLOCKER"
    # Check for alternative databases
    find ~/Library/Mail -name "*.db" -o -name "*.sqlite" 2>/dev/null
else
    echo "✅ Found: $MAIL_DB"
    sqlite3 "$MAIL_DB" ".tables" 2>&1
    sqlite3 "$MAIL_DB" "PRAGMA table_info(messages);" 2>&1
    sqlite3 "$MAIL_DB" "SELECT COUNT(*) FROM messages;" 2>&1
    # Function to detect which column exists and print sample messages
    print_sample_messages() {
        local db="$1"
        if sqlite3 "$db" "PRAGMA table_info(messages);" | grep -q '|snippet|'; then
            # Use 'snippet' column if available
            sqlite3 "$db" "SELECT ROWID, subject, sender, snippet FROM messages ORDER BY ROWID DESC LIMIT 5;"
        elif sqlite3 "$db" "PRAGMA table_info(messages);" | grep -q '|summary|'; then
            # Use 'summary' column if available
            sqlite3 "$db" "SELECT ROWID, subject, sender, summary FROM messages ORDER BY ROWID DESC LIMIT 5;"
        else
            echo "⚠️ Neither snippet nor summary column found in Mail messages table"
        fi
    }
    sqlite3 ~/Library/Messages/chat.db 'SELECT ROWID,text,is_from_me FROM message ORDER BY ROWID DESC LIMIT 5;' 2>&1
    print_sample_messages "$MAIL_DB"
fi

echo ""
echo "=== Messages Database ==="
if sqlite3 ~/Library/Messages/chat.db 'SELECT COUNT(*) FROM message;' 2>/dev/null; then
    echo "✅ Messages DB readable"
    sqlite3 ~/Library/Messages/chat.db 'SELECT ROWID,text,is_from_me FROM message ORDER BY ROWID DESC LIMIT 5;' 2>&1
else
    echo "⛔ Messages DB NOT readable — iMessage commands will not work"
fi

echo ""
echo "=== AppleScript Messages ==="
# The following AppleScript command lists all available iMessage services (e.g., iMessage, AIM, Jabber) in the Messages app.
osascript -e 'tell application "Messages" to get every service' 2>&1
echo "(If you see services listed, AppleScript access works)"

echo ""
echo "=== Docker ==="
docker version 2>&1 || echo "⚠️ Docker not running or not installed"

echo ""
echo "=== Ollama ==="
ollama --version 2>&1 || echo "⚠️ Ollama not installed"
curl -sf http://127.0.0.1:11434/api/tags 2>&1 || echo "⚠️ Ollama not serving"

echo ""
echo "=== Python ==="
PYTHON_BIN="$(command -v python3)"
if [ -n "$PYTHON_BIN" ]; then
  "$PYTHON_BIN" --version
else
  echo "⚠️ python3 not found"
fi
echo ""
echo "=== Memory ==="
# Assumes sysctl hw.memsize returns bytes as the second field; if format changes, update parsing accordingly.
MEM_LINE=$(sysctl hw.memsize)
MEM_BYTES=$(echo "$MEM_LINE" | awk '{print $2}')
if [[ "$MEM_BYTES" =~ ^[0-9]+$ ]]; then
    echo "Total RAM: $(awk "BEGIN {printf \"%.2f\", $MEM_BYTES/1024/1024/1024}") GB"
                                            else
                                            echo "⚠️ Unexpected hw.memsize output: $MEM_LINE"
                                            fi
memory_pressure 2>&1 | head -5

echo ""
echo "=== Disk ==="
df -h / | tail -1