#!/bin/bash
MAIL_DB="$HOME/Library/Mail/V10/MailData/Envelope Index"

echo "=== ULTIMATE QUERY ==="
sqlite3 "$MAIL_DB" "
SELECT 
    m.ROWID,
    sub.subject,
    a.address as sender_email,
    a.comment as sender_name,
    substr(summ.summary, 1, 200) as snippet_preview,
    m.date_received,
    mb.url as mailbox_url,
    mgd.message_id_header,
    mgd.model_category,
    mgd.urgent
FROM messages m
LEFT JOIN subjects sub ON m.subject = sub.ROWID
LEFT JOIN sender_addresses sa ON sa.sender = m.sender
LEFT JOIN addresses a ON sa.address = a.ROWID
LEFT JOIN summaries summ ON m.summary = summ.ROWID
LEFT JOIN mailboxes mb ON m.mailbox = mb.ROWID
LEFT JOIN message_global_data mgd ON m.global_message_id = mgd.ROWID
ORDER BY m.ROWID DESC
LIMIT 5;
"

echo ""
echo "=== SENDER ADDRESS CHECK (is sender_addresses join working?) ==="
sqlite3 "$MAIL_DB" "
SELECT 
    m.ROWID,
    m.sender as sender_fk,
    sa.address as sa_address_fk,
    a.address as actual_email,
    a.comment as display_name
FROM messages m
LEFT JOIN sender_addresses sa ON sa.sender = m.sender
LEFT JOIN addresses a ON sa.address = a.ROWID
ORDER BY m.ROWID DESC
LIMIT 10;
"

echo ""
echo "=== COUNT CHECK ==="
sqlite3 "$MAIL_DB" "
SELECT 
    (SELECT COUNT(*) FROM messages) as total_messages,
    (SELECT COUNT(*) FROM messages m LEFT JOIN sender_addresses sa ON sa.sender = m.sender LEFT JOIN addresses a ON sa.address = a.ROWID WHERE a.address IS NOT NULL) as messages_with_sender_email,
    (SELECT COUNT(*) FROM messages m LEFT JOIN summaries summ ON m.summary = summ.ROWID WHERE summ.summary IS NOT NULL) as messages_with_summary;
"

echo ""
echo "=== APPLE ML CATEGORIES DISTRIBUTION ==="
sqlite3 "$MAIL_DB" "
SELECT 
    mgd.model_category,
    COUNT(*) as count
FROM messages m
LEFT JOIN message_global_data mgd ON m.global_message_id = mgd.ROWID
GROUP BY mgd.model_category
ORDER BY count DESC;
"

echo ""
echo "=== PYTHON PATH ==="
which python3
python3 -c "import sys; print(sys.executable)"
python3 -c "import tomllib, sqlite3; print('stdlib OK')"