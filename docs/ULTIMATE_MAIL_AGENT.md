# ULTIMATE MAIL AGENT — Implementation Plan

Mac-Resident AI Mail Agent + Synology Backend

---

## 1. Architecture

Mac = Brain  
Synology = Storage + Dashboard  

Flow:

Email (Gmail / Outlook / iCloud)
→ Mac Mini (mailagent)
→ Local LLM (Ollama / MLX - Gemma)
→ Actions:
  - classify email
  - generate draft
  - extract PDF
  - decrypt + rename
  - send notification
→ Synology NAS
  - SQLite DB
  - PDF storage
  - dashboard

---

## 2. Components

### Mac Mini
- Python agent (mailagent)
- Ollama / MLX
- IMAP email fetch
- pikepdf (PDF decrypt)
- AppleScript (iMessage)
- Writes to NAS SQLite

### Synology NAS
- Mounted at /Volumes/Synology
- SQLite database
- PDF archive
- Dashboard (React / API)

---

## 3. Folder Structure

Mac:
~/agentic-ai/
  mailagent/
    main.py
    config.yaml
    pdf/
    llm/
    notifications/
    state/

NAS:
/Volumes/Synology/mailagent/
  db.sqlite
  logs/
  pdf/
    invoices/
    statements/

---

## 4. Database (SQLite)

email_decisions:
- id
- message_id
- subject
- sender
- classification
- action
- created_at

pdf_jobs:
- id
- message_id
- original_filename
- new_filename
- status
- path
- created_at

notifications:
- id
- message_id
- type
- status
- created_at

checkpoints:
- id
- last_success_timestamp

errors:
- id
- stage
- error
- created_at

---

## 5. Main Loop

while True:
  check network
  fetch emails after checkpoint
  process emails
  update checkpoint ONLY if success
  sleep

---

## 6. Critical Rules

1. No silent failure
2. Never update checkpoint unless FULL success
3. Idempotent processing (use message_id)

---

## 7. LLM Output Format

{
  "category": "invoice | personal | spam",
  "action": "notify | archive | ignore",
  "pdf_required": true,
  "filename": "YYYY-MM-DD_vendor_type.pdf"
}

---

## 8. PDF Pipeline

1. Extract attachment
2. Decrypt:

import pikepdf
pdf = pikepdf.open("file.pdf", password="password")
pdf.save("clean.pdf")

3. Rename:
YYYY-MM-DD_VENDOR_TYPE.pdf

4. Move:
/Volumes/Synology/mailagent/pdf/invoices/

---

## 9. iMessage Notification

osascript -e 'tell application "Messages" to send "New invoice received" to buddy "+628xxxx"'

---

## 10. launchd (Auto Run)

File:
~/Library/LaunchAgents/com.mailagent.plist

<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.mailagent</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/g4ndr1k/agentic-ai/mailagent/main.py</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/tmp/mailagent.log</string>

  <key>StandardErrorPath</key>
  <string>/tmp/mailagent.err</string>
</dict>
</plist>

Load:

launchctl load ~/Library/LaunchAgents/com.mailagent.plist

---

## 11. Network Guard

import socket

def network_ok():
  try:
    socket.create_connection(("1.1.1.1", 53), timeout=3)
    return True
  except:
    return False

---

## 12. Failure Strategy

IMAP fail → retry  
LLM fail → retry once  
PDF fail → log  
NAS fail → retry + block checkpoint  
Notification fail → retry async  

---

## 13. Dashboard (NAS)

- React + Tailwind
- API (FastAPI / Node)
- Reads SQLite

Features:
- email logs
- PDF tracking
- error monitor

---

## 14. Execution Phases

Phase 1:
- IMAP + checkpoint + SQLite

Phase 2:
- LLM classification

Phase 3:
- PDF pipeline

Phase 4:
- iMessage

Phase 5:
- Dashboard

Phase 6:
- WhatsApp (optional)

---

## FINAL

Mac = compute + automation  
NAS = storage + visibility  

