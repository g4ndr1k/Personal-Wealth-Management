# Phase 4 — Smart Rules & AI Classification Engine

**Date:** 2026-04-29
**Scope:** mail-agent (`agent/app/`)
**Status:** Plan only — ready for handoff after sign-off

---

## Context

The mail-agent today (after Phase A.5 / B) ingests IMAP messages, classifies them via the static `classifier.py` (Finance API + TOML rules), sends iMessage alerts via the bridge, and routes PDF attachments through `pdf_router.py` (which already uses Ollama). There is no per-account customization, no user-managed rules, and no structured AI enrichment. Phase 4 introduces:

1. A **deterministic SQLite-backed rules engine** (the "Screener") that runs before classification — fast, zero-inference, user-editable from the dashboard.
2. An **Ollama-backed AI triage** (the "Triage") that runs only on messages the Screener does not discard, producing structured classifications used to drive notifications and routing.

This unblocks the Settings UI work (per `merged-mail-agent-email-settings-plan-2026-04-29.md`) and gives users meaningful control over which mail produces alerts vs. silent archival.

---

## Validation Against Current Code

The original draft made several assumptions that do **not** match the repo. The plan below is the corrected version.

| Original assumption | Reality (file:line) | Resolution in this plan |
|---|---|---|
| WAL + `foreign_keys = ON` already set | [state.py:17](agent/app/state.py#L17) sets only `busy_timeout=5000`; default `journal_mode=DELETE`, FK off | Add a one-time pragma block in `_init_db()` and on every connection helper |
| IMAP supports MOVE / CREATE / capability checks | [imap_source.py:320](agent/app/imap_source.py#L320) opens SELECT readonly; no mutations exist | Phase 4A ships **no IMAP mutations**. `move_to_folder` / `add_label` are deferred to 4B once mutation helpers + capability probe land |
| Ollama already uses `format` JSON schema | [pdf_router.py:216](agent/app/pdf_router.py#L216) calls `/api/generate` with `stream:false` and prompt-engineered JSON only | Phase 4B uses `format=<json-schema>` on `/api/chat` for the new classifier; PDF router is left alone |
| `send_imessage` exists | [bridge_client.py:64](agent/app/bridge_client.py#L64) only has `send_alert(text)` | Reuse `send_alert`. No new bridge endpoint. The "action" is just a templated alert with cooldown |
| TOML keys `[mail.processing]` / `[mail.ai]` | Existing convention in [settings.toml](config/settings.toml) is mixed (`[mail.imap]`, `[mail_agent.pdf]`) | Standardize new sections under `[mail.processing]` and `[mail.ai]` (follows `[mail.imap]`). Leave `[mail_agent.pdf]` alone — out of scope |
| Atomic TOML write path exists | [config_manager.py:49](agent/app/config_manager.py#L49) `_atomic_write_toml` — temp+fsync+rename with EBUSY fallback | ✅ Reuse as-is for `PUT /api/mail/ai/settings` |
| Hook point for the rules engine | [orchestrator.py:248-263](agent/app/orchestrator.py#L248) dedup, then classifier at L266 | ✅ Insert evaluator between dedup and classifier |
| API auth pattern | [api_mail.py:73](agent/app/api_mail.py#L73) `require_api_key` via `X-Api-Key` + `hmac.compare_digest` | ✅ Reuse on every new endpoint |

---

## 1. Storage

### 1.1 Static engine defaults — `config/settings.toml`

TOML holds engine toggles + Ollama connection only. Mutable through `PUT /api/mail/ai/settings` via the existing `_atomic_write_toml` path in [config_manager.py](agent/app/config_manager.py).

```toml
[mail.processing]
enabled = true
poll_interval_seconds = 300
batch_size = 50
process_unread_only = true

[mail.ai]
enabled = false                   # 4B opt-in; 4A ships with this off
provider = "ollama"
base_url = "http://host.docker.internal:11434"
model = "qwen2.5:7b-instruct-q4_K_M"
temperature = 0.1
timeout_seconds = 45
max_body_chars = 12000
urgency_threshold = 8
```

`config_manager.py` gets two new helpers (`get_ai_settings`, `update_ai_settings`) mirroring the existing IMAP-account helpers; both go through the same atomic-write path so Docker EBUSY is handled.

### 1.2 Dynamic state — SQLite (`data/agent.db`, mail-agent namespace)

Phase 4A mail-agent runtime state lives in `data/agent.db`, not
`data/finance.db`. This keeps mail rules, rule actions, rule audit events,
needs-reply rows, and future AI queue/classification state with the mail-agent
runtime. `data/finance.db` remains the authoritative PWM/finance data store.

**Connection contract.** Every connection opened from `state.py` (and any new helper) must run, in order:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

`journal_mode = WAL` is a database-level persistent setting (set once); `foreign_keys` and `busy_timeout` are per-connection and must be re-set every time. A unit test asserts `PRAGMA foreign_keys` returns `1` and `PRAGMA journal_mode` returns `wal` on a fresh connection.

**Tables (new migration in [state.py](agent/app/state.py)):**

- `mail_rules` — `rule_id, account_id NULL, name, priority, enabled, match_type, created_at, updated_at`
  - `CHECK(match_type IN ('ALL','ANY')) CHECK(enabled IN (0,1))`
  - `UNIQUE INDEX idx_mail_rules_account_priority ON mail_rules(COALESCE(account_id,'__global__'), priority)` — uses `COALESCE` to escape SQLite's NULL-uniqueness gotcha
- `mail_rule_conditions` — `id, rule_id FK CASCADE, field, operator, value, value_json, case_sensitive`
  - `UNIQUE(rule_id, field, operator, value)`
- `mail_rule_actions` — `id, rule_id FK CASCADE, action_type, target, value_json, stop_processing`
- `mail_ai_queue` — `id, account_id, message_id, folder, imap_uid, uidvalidity, body_hash, status, attempts, next_attempt_at, last_error, created_at, updated_at, manual_nonce NULL`
  - `UNIQUE(account_id, folder, uidvalidity, imap_uid, body_hash, COALESCE(manual_nonce,''))`
  - `CHECK(status IN ('pending','running','completed','failed','skipped')) CHECK(attempts >= 0)`
- `mail_ai_classifications` — `id, queue_id FK, category, urgency_score, confidence, summary, raw_json, created_at`
  - `CHECK(urgency_score BETWEEN 0 AND 10) CHECK(confidence BETWEEN 0 AND 1)`
- `mail_ai_categories`, `mail_ai_trigger_rules` — future AI lookup/trigger tables in `agent.db`.
- `mail_processing_events` — rule audit events in `agent.db`.
- `mail_needs_reply` — needs-reply rows in `agent.db`.

The migration runs idempotently on agent start, beside the existing `imap_accounts` / `pdf_attachments` migrations in [state.py](agent/app/state.py).

---

## 2. Transaction & I/O Boundary

WAL allows concurrent readers but only one writer. The hard rule everywhere in Phase 4 code:

> **No DB transaction may remain open across an IMAP / Ollama / bridge call.**

Required pattern for every worker step:

1. Open short write txn → claim work (`status='pending' → 'running'`) → commit.
2. Run external I/O (Ollama / IMAP read / bridge) outside any transaction.
3. Open new short write txn → record result, increment attempts, write audit event → commit.

This is enforced by code review, not a tool. `state.py` should expose helper context managers (`claim_queue_item`, `complete_queue_item`) that hide the boundaries so callers can't accidentally hold a connection across `await`.

---

## 3. Evaluator Semantics

- Rules evaluated in ascending `priority`. `enabled=0` skipped. Global rules (`account_id IS NULL`) interleave by priority with per-account rules — the unique index uses `COALESCE` so collisions are caught.
- Each action runs independently; one failure does not roll back prior successful actions, but all outcomes log to `mail_processing_events` inside their own txn.
- `stop_processing=true` halts further deterministic rules. `skip_ai_inference` halts AI enqueue. Both flags are applied **after** the matching rule's actions finish.
- Hook point: between [orchestrator.py:263](agent/app/orchestrator.py#L263) (dedup confirmed) and [orchestrator.py:266](agent/app/orchestrator.py#L266) (classifier call). The evaluator returns `(actions_executed, continue_to_classifier: bool, enqueue_ai: bool)`.

### Idempotency & manual reprocess

- A message is keyed by `(account_id, folder, uidvalidity, imap_uid, body_hash)` — same shape as the existing `make_fallback_key` in [imap_source.py:128](agent/app/imap_source.py#L128).
- `POST /api/mail/messages/{message_id}/reprocess` writes a new queue row with a fresh `manual_nonce` (uuid4) so the unique index allows it.

---

## 4. Safe Actions — v1 Scope

**Phase 4A ships only the actions that need no IMAP mutation:**

- `mark_pending_alert` (writes to `mail_processing_events`; orchestrator already calls bridge)
- `skip_ai_inference`
- `add_to_needs_reply` (rows in a new `mail_needs_reply` view-table; surfaced by Settings UI)
- `route_to_pdf_pipeline` (already wired in [pdf_router.py](agent/app/pdf_router.py); rule just sets a flag the orchestrator reads at L348)
- `notify_dashboard` (Electron event via existing dashboard API)
- `stop_processing` flag on the rule itself

**Deferred to 4B (requires IMAP mutation helpers + CAPABILITY probe in [imap_source.py](agent/app/imap_source.py)):**
`move_to_folder`, `add_label`, `mark_read`, `mark_flagged`.

**Deferred to 4C:** `send_imessage` (uses existing `bridge_client.send_alert` with cooldown/dedupe), `delete`, `auto_reply`, `forward`, `unsubscribe`, `external_webhook`.

---

## 5. API Surface

All under [api_mail.py](agent/app/api_mail.py), router prefix `/api/mail`, every endpoint guarded by the existing `require_api_key` dependency.

**Rules CRUD + preview:**
`GET/POST /rules`, `GET/PATCH/DELETE /rules/{id}`, `PUT /rules/reorder`, `POST /rules/preview` (returns `{matched_conditions, planned_actions, would_skip_ai}` without mutating state).

**AI settings & test:**
`GET/PUT /ai/settings`, `POST /ai/test` (sends a synthetic message body to Ollama, returns parsed JSON or validation error).

**AI categories & triggers:** standard CRUD under `/ai/categories` and `/ai/triggers`.

**Audit:** `GET /messages/{message_id}/processing-events`, `POST /messages/{message_id}/reprocess`.

---

## 6. Rollout

**Phase 4A — deterministic engine, no IMAP mutations**
- Migration (pragmas + new tables) in `state.py`.
- Evaluator module (new file `agent/app/rules.py`) called from `orchestrator._scan_imap_once`.
- Rules CRUD + preview API in `api_mail.py`.
- Settings UI rule editor (separate frontend ticket).
- Tests: pragma assertion, priority ordering with global+per-account interleave, ALL vs ANY match, `stop_processing` halt, audit log written per outcome, idempotency under repeated runs.

**Phase 4B — AI enrichment as read-only**
- AI settings API + atomic TOML write.
- `mail_ai_queue` worker (single asyncio task; one in-flight request to Ollama).
- Ollama call uses `/api/chat` with `format=<json-schema>` and `stream=false`; output validated through a Pydantic model before write. Validation failure → `status='failed'`, `last_error` populated, no triggers fire.
- Dashboard surfaces `summary`, `category`, `urgency_score` and the manual reprocess action.

**Phase 4C — AI trigger actions + IMAP mutations**
- IMAP capability probe + UID-based MOVE / STORE helpers in `imap_source.py`.
- Promote `move_to_folder` / `add_label` / `mark_read` / `mark_flagged` into the safe-action list.
- AI trigger CRUD wired to `bridge_client.send_alert` with per-trigger cooldown table.
- Populate "Needs Reply" materialized view consumed by the Electron dashboard.

---

## Critical Files to Modify

- [agent/app/state.py](agent/app/state.py) — pragmas, new tables, queue helpers
- [agent/app/orchestrator.py](agent/app/orchestrator.py) — evaluator hook between L263 and L266
- [agent/app/api_mail.py](agent/app/api_mail.py) — new endpoints under existing router
- [agent/app/config_manager.py](agent/app/config_manager.py) — `get_ai_settings`, `update_ai_settings`
- [agent/app/rules.py](agent/app/rules.py) — **new**: evaluator
- [agent/app/ai_worker.py](agent/app/ai_worker.py) — **new** in 4B: queue runner
- [config/settings.toml](config/settings.toml) — `[mail.processing]`, `[mail.ai]` sections
- [agent/app/imap_source.py](agent/app/imap_source.py) — **4C only**: capability probe + MOVE/STORE

## Functions / Patterns to Reuse

- `_atomic_write_toml` in [config_manager.py:49](agent/app/config_manager.py#L49) — for TOML mutation
- `require_api_key` in [api_mail.py:73](agent/app/api_mail.py#L73) — auth on all new endpoints
- `make_message_key` / `make_fallback_key` in [imap_source.py:122](agent/app/imap_source.py#L122) — idempotency keying
- `send_alert` in [bridge_client.py:64](agent/app/bridge_client.py#L64) — sole alert path
- Existing `pdf_router.process_attachment` flow at [orchestrator.py:348](agent/app/orchestrator.py#L348) — `route_to_pdf_pipeline` is just a flag toggle, not new code

---

## Verification

**Unit / integration:**
- `pytest agent/tests/test_state_pragmas.py` — assert `foreign_keys=1` and `journal_mode=wal` on a fresh connection.
- `pytest agent/tests/test_rules_engine.py` — priority ordering across global+per-account, ALL vs ANY, `stop_processing`, audit-log emission, idempotent re-runs.
- `pytest agent/tests/test_ai_queue.py` (4B) — schema-validated Ollama output, failure path increments attempts, manual reprocess bypasses unique constraint via `manual_nonce`.

**End-to-end (4A acceptance):**
1. `docker compose up --build -d` (per CLAUDE.md — restart alone won't pick up code changes).
2. `curl -H "X-Api-Key: $FINANCE_API_KEY" -X POST http://localhost:8090/api/mail/rules -d '{...}'` to create a rule that fires `notify_dashboard` for a known sender.
3. Trigger a poll cycle (`POST /run`) and confirm:
   - `mail_processing_events` has matched-rule + action-completed rows.
   - Existing classifier/alert path is unaffected for unmatched messages.
4. `POST /api/mail/rules/preview` against a sample message returns the expected matched conditions without writing any audit row.
5. `GET /api/mail/messages/{id}/processing-events` returns the trail for step 3.

**4B acceptance adds:** `POST /api/mail/ai/test` with a fixture body returns valid JSON; queue table moves item from `pending → running → completed`; reprocess endpoint creates a second completed row with the same body_hash.
