#!/usr/bin/env bash
set -euo pipefail

DB="${AGENT_DB_PATH:-data/agent.db}"
WHAT="${1:-all}"
LIMIT="${2:-20}"

if [[ ! -f "$DB" ]]; then
  echo "agent DB not found: $DB" >&2
  exit 1
fi

if [[ ! "$LIMIT" =~ ^[0-9]+$ ]]; then
  echo "limit must be a positive integer" >&2
  exit 2
fi

case "$WHAT" in
  rules)
    sqlite3 -header -column "$DB" "
      SELECT
        r.rule_id,
        COALESCE(r.account_id, '__global__') AS account_id,
        r.priority,
        r.enabled,
        r.match_type,
        r.name,
        COUNT(DISTINCT c.id) AS conditions,
        COUNT(DISTINCT a.id) AS actions,
        r.updated_at
      FROM mail_rules r
      LEFT JOIN mail_rule_conditions c ON c.rule_id = r.rule_id
      LEFT JOIN mail_rule_actions a ON a.rule_id = r.rule_id
      GROUP BY r.rule_id
      ORDER BY r.priority, r.rule_id;
    "
    ;;
  events)
    sqlite3 -header -column "$DB" "
      SELECT
        id,
        created_at,
        message_id,
        account_id,
        rule_id,
        action_type,
        event_type,
        outcome
      FROM mail_processing_events
      ORDER BY id DESC
      LIMIT $LIMIT;
    "
    ;;
  needs-reply)
    sqlite3 -header -column "$DB" "
      SELECT
        id,
        updated_at,
        status,
        account_id,
        sender_email,
        subject,
        message_id
      FROM mail_needs_reply
      ORDER BY id DESC
      LIMIT $LIMIT;
    "
    ;;
  all)
    "$0" rules "$LIMIT"
    echo
    "$0" events "$LIMIT"
    echo
    "$0" needs-reply "$LIMIT"
    ;;
  *)
    echo "usage: $0 [rules|events|needs-reply|all] [limit]" >&2
    exit 2
    ;;
esac
