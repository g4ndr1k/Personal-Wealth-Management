#!/bin/bash
MAIL_DB="$HOME/Library/Mail/V10/MailData/Envelope Index"

echo "=== 1. What tables reference addresses? ==="
sqlite3 "$MAIL_DB" "
SELECT name, sql FROM sqlite_master 
WHERE type='table' AND sql LIKE '%address%'
ORDER BY name;
"

echo ""
echo "=== 2. Check if addresses.ROWID matches senders.ROWID directly ==="
sqlite3 "$MAIL_DB" "
SELECT 
    s.ROWID as sender_rowid,
    a.ROWID as addr_rowid,
    a.address,
    a.comment
FROM senders s
JOIN addresses a ON a.ROWID = s.ROWID
ORDER BY s.ROWID DESC
LIMIT 10;
"

echo ""
echo "=== 3. Check if messages.sender maps directly to addresses.ROWID ==="
sqlite3 "$MAIL_DB" "
SELECT 
    m.ROWID as msg_rowid,
    m.sender as sender_fk,
    a.address,
    a.comment
FROM messages m
JOIN addresses a ON a.ROWID = m.sender
ORDER BY m.ROWID DESC
LIMIT 10;
"

echo ""
echo "=== 4. What does sender FK 4897 (GitHub) map to in each table? ==="
echo "--- senders ---"
sqlite3 "$MAIL_DB" "SELECT ROWID, * FROM senders WHERE ROWID = 4897;"
echo "--- addresses ---"
sqlite3 "$MAIL_DB" "SELECT ROWID, * FROM addresses WHERE ROWID = 4897;"
echo "--- sender_addresses where sender=4897 ---"
sqlite3 "$MAIL_DB" "SELECT * FROM sender_addresses WHERE sender = 4897;"
echo "--- sender_addresses where address=4897 ---"
sqlite3 "$MAIL_DB" "SELECT * FROM sender_addresses WHERE address = 4897;"

echo ""
echo "=== 5. What does sender FK 7361 (VCS/CME) map to? ==="
echo "--- senders ---"
sqlite3 "$MAIL_DB" "SELECT ROWID, * FROM senders WHERE ROWID = 7361;"
echo "--- addresses ---"
sqlite3 "$MAIL_DB" "SELECT ROWID, * FROM addresses WHERE ROWID = 7361;"

echo ""
echo "=== 6. Recent message coverage check ==="
sqlite3 "$MAIL_DB" "
SELECT 
    m.ROWID,
    sub.subject,
    a_direct.address as direct_addr,
    a_direct.comment as direct_name,
    CASE WHEN summ.summary IS NOT NULL THEN 'HAS_BODY' ELSE 'NO_BODY' END as body_status,
    m.date_received
FROM messages m
LEFT JOIN subjects sub ON m.subject = sub.ROWID
LEFT JOIN addresses a_direct ON a_direct.ROWID = m.sender
LEFT JOIN summaries summ ON m.summary = summ.ROWID
ORDER BY m.ROWID DESC
LIMIT 15;
"

echo ""
echo "=== 7. Coverage for last 100 messages ==="
sqlite3 "$MAIL_DB" "
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN a.address IS NOT NULL THEN 1 ELSE 0 END) as has_sender_email,
    SUM(CASE WHEN summ.summary IS NOT NULL THEN 1 ELSE 0 END) as has_body
FROM (SELECT * FROM messages ORDER BY ROWID DESC LIMIT 100) m
LEFT JOIN addresses a ON a.ROWID = m.sender
LEFT JOIN summaries summ ON m.summary = summ.ROWID;
"

echo ""
echo "=== 8. iMessage send test preparation ==="
echo "Python: $(python3 -c 'import sys; print(sys.executable)')"
echo "tomllib: $(python3 -c 'import tomllib; print("OK")' 2>&1)"

echo ""
echo "=== 9. Conda env info ==="
conda info --base 2>/dev/null || echo "conda info failed"
python3 --version