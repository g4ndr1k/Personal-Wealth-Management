# Phase 4F Natural Language Rule Builder

Design status: Phase 4F.1a implemented for deterministic sender suppression drafts only; later phases remain planned. It does not enable live Gmail/IMAP mutation, autonomous execution, bulk approval, or auto-execute after approval.

Phase 4F lets the user type natural-language requests such as:

```text
Add abcd@efcf.com to the spam list
If the mail is from Permata Bank asking for clarification on credit card transaction, send me an iMessage notification
```

The system uses AI to draft a proposed deterministic mail rule, then shows the final rule to the user for review and approval before saving. The existing deterministic rules engine executes saved rules later.

Core principle:

```text
AI suggests -> deterministic system validates -> human approves -> safe engine applies
```

AI must not directly save rules, execute mailbox actions, or mutate email.

## Suggested Phase Split

| Phase | Scope |
|---|---|
| `4F.1a` | Implemented: deterministic/local single-sender suppression drafts only. |
| `4F.1` | Planned remainder: broader AI rule drafting, deterministic validation, preview, and human save for safe non-mutating rule actions only. |
| `4F.2` | Rule explanation and conflict detection for duplicates, priority collisions, shadowing, contradictions, and unsafe actions. |
| `4F.3` | AI-assisted rule refinement from examples, such as "make this rule match these messages but not those." |

Phase 4F can safely happen before Phase 4E.3 because it improves rule authoring usability without increasing live mailbox mutation risk. Phase 4E remains the execution safety model for any future live reversible mailbox mutation.

## Relationship To Phase 4E

Phase 4F does not replace Phase 4E.

Phase 4E is about approved action execution and future live reversible mailbox mutations. Phase 4F is about user-friendly rule authoring.

Future live actions such as `mark_read`, `mark_unread`, `add_label`, `move_to_folder`, or spam mutation must still go through the Phase 4E approval/execution safety model when they eventually exist. Phase 4F.1 does not promote those actions to live behavior.

Current safety baseline remains:

- No live Gmail/IMAP mutation is enabled by Phase 4F.
- No autonomous execution.
- No bulk approval or bulk execute.
- No auto-execute after approval.
- No retry loop that can mutate.
- Dangerous actions remain blocked.
- `add_label`, `move_to_folder`, `mark_read`, `mark_unread`, and Gmail spam/junk mutation remain blocked or deferred for Phase 4F.1 rule drafting.

## Architecture

Natural-language rule builder flow:

```text
User natural-language request
  -> AI rule drafting
  -> deterministic schema validation
  -> safety allow-list validation
  -> preview/diff
  -> human approve/save
  -> existing deterministic rules engine
```

This is not autonomous AI execution. AI drafts only. Deterministic code validates and normalizes. The human saves. The deterministic rules engine applies saved rules later.

## Implemented 4F.1a MVP

`agent/app/rule_ai_builder.py` implements the first slice without an LLM path. The implementation is deterministic and local by default.

Supported requests are limited to one sender email address and local suppression intent vocabulary:

- `spam list`
- `block` / `block alerts`
- `suppress`
- `stop processing`
- `ignore`
- `mute`

The only saveable draft shape is:

- condition: `from_email equals <normalized email>`
- actions: `skip_ai_inference` and `stop_processing`
- `safety_status`: `safe_local_suppression`
- `requires_user_confirmation`: `true`

`POST /api/mail/rules/ai/draft` returns the draft only. It does not write `mail_rules`, `mail_rule_conditions`, or `mail_rule_actions`. Saving remains a separate human-triggered call to the existing `POST /api/mail/rules` endpoint.

The phrase “spam list” currently means local Mail Agent suppression, not Gmail Spam. The builder does not mutate Gmail, does not call IMAP, and does not execute mailbox actions.

The 4F.1a input is intentionally narrow: one single-line string up to 1000 characters, exactly one valid sender email address, optional `account_id` carried as draft metadata only, and no comma/semicolon-separated multi-recipient requests.

The MVP blocks/returns unsupported for explicit live mailbox requests including:

- move to spam
- delete
- archive
- mark read
- mark unread
- label

## Implemented 4F.1b Local LLM Alert Probe

Phase 4F.1b adds a local Ollama-only capability probe behind `[mail.rule_ai]`. It is disabled by default. The probe drafts alert rules only and is meant to help inspect whether the local model can reliably convert a narrow natural-language request into a safe rule draft. Phase 4F.1c hardens that same path with expanded deterministic Indonesian bank/domain hints and bilingual alert-intent normalization; it does not add mailbox mutation, autonomous execution, cloud LLMs, or automatic saving.

`POST /api/mail/rules/ai/draft` now accepts:

- `mode=auto`
- `mode=sender_suppression`
- `mode=alert_rule`

Backwards-compatible requests without `mode` still support deterministic 4F.1a sender suppression. `alert_rule` uses the local LLM only when `[mail.rule_ai].enabled=true`; otherwise it returns an unsupported response and no saveable rule.

The Ollama `/api/chat` call uses structured JSON schema output where supported. The schema requires object-shaped `rule`, array-shaped `rule.conditions`, array-shaped `rule.actions`, and array-shaped `explanation` / `warnings`. This is a model-guidance layer only; deterministic post-validation remains authoritative.

The only saveable Phase 4F.1b alert shape is:

- `match_type`: `ALL`
- conditions: `from_domain contains <domain>` or `from_email equals <email>`
- content condition: at least one `subject contains <keyword>` or `body contains <keyword>`
- action: `mark_pending_alert`, target `imessage`, `stop_processing=false`
- `safety_status`: `safe_local_alert_draft`
- `requires_user_confirmation`: `true`

Known bank-domain hints are deterministic and local:

```python
BANK_DOMAIN_HINTS = {
    "permata": "permatabank.co.id",
    "permata bank": "permatabank.co.id",
    "bca": "bca.co.id",
    "klikbca": "klikbca.com",
    "klik bca": "klikbca.com",
    "cimb": "cimbniaga.co.id",
    "cimb niaga": "cimbniaga.co.id",
    "maybank": "maybank.co.id",
    "mandiri": "bankmandiri.co.id",
    "bank mandiri": "bankmandiri.co.id",
    "livin": "bankmandiri.co.id",
    "livin mandiri": "bankmandiri.co.id",
    "bni": "bni.co.id",
    "bank negara indonesia": "bni.co.id",
    "bri": "bri.co.id",
    "bank rakyat indonesia": "bri.co.id",
    "ocbc": "ocbc.id",
    "ocbc nisp": "ocbc.id",
    "uob": "uob.co.id",
    "hsbc": "hsbc.co.id",
    "dbs": "dbs.id",
    "jenius": "jenius.com",
    "bsi": "bankbsi.co.id",
    "bank syariah indonesia": "bankbsi.co.id",
}
```

For known bank names, post-validation normalizes the draft domain to the deterministic hint and overrides model-supplied domains. If a request does not name a supported bank and does not include one explicit sender email or domain, the alert draft is unsupported rather than trusting a model-guessed sender.

Phase 4F.1c expands content keyword normalization. For common Indonesian/English alert intents, post-processing ensures at least one relevant content condition exists and may add one or two conservative subject keywords:

- credit-card clarification/confirmation: `clarification`, `klarifikasi`, `confirmation`, `konfirmasi`, `credit card`, `kartu kredit`, `transaction`, `transaksi`, `verification`, `verifikasi`
- suspicious/security/login alerts: `security`, `keamanan`, `suspicious`, `mencurigakan`, `login`, `activity`, `aktivitas`, `transaction`, `transaksi`, `alert`, `peringatan`
- payment due/billing: `payment`, `pembayaran`, `due`, `jatuh tempo`, `bill`, `billing`, `tagihan`, `statement`, `e-statement`, `kartu kredit`
- OTP/verification-code: `otp`, `one time password`, `verification code`, `kode verifikasi`, `authentication`, `autentikasi`
- failed/declined transactions: `declined`, `ditolak`, `failed`, `gagal`, `transaction`, `transaksi`

Alert drafts are capped at six conditions. They must still include at least one sender condition and at least one content condition, and they must use `match_type=ALL`.

The LLM output is post-validated deterministically. Unsupported or unsafe outputs return `status=unsupported`, `saveable=false`, no rule, and short sanitized error text when relevant. Useful unsupported reason codes include `missing_sender_condition`, `missing_content_condition`, `unsupported_action`, `ambiguous_bank_domain`, `low_confidence`, and `invalid_model_schema`. The endpoint does not write `mail_rules`, `mail_rule_conditions`, or `mail_rule_actions`; it does not call IMAP, call mailbox execution code, mutate Gmail, call the bridge, send iMessage, auto-save, or auto-execute. Cloud LLM integration remains deferred.

## Phase 4F.1c Golden Prompt Fixtures

These prompts are useful for manual local-Qwen checks. Normal automated tests use fake LLM clients and do not call Ollama.

1. If BCA emails me about suspicious transaction, notify me.
2. If CIMB Niaga asks for credit card transaction confirmation, send me an iMessage notification.
3. Notify me if Maybank sends a security alert.
4. If Permata asks me to confirm a kartu kredit transaction, alert me.
5. If the email is from klikbca about login/security, notify me.
6. Notify me if Mandiri sends an OTP email.
7. If BNI sends a failed transaction email, alert me.
8. If BRI sends a payment due notice, notify me.
9. If OCBC sends a suspicious login email, notify me.
10. If Jenius sends an account security alert, alert me.

## Implemented 4F.1d Local Golden Smoke Harness

Phase 4F.1d makes the golden prompts repeatable through an explicit operator-run smoke harness:

```text
agent/tests/fixtures/rule_ai_golden_prompts.json
scripts/mail_rule_ai_golden_probe.py
```

The fixture records each prompt, expected deterministic bank domain, expected action (`mark_pending_alert`), expected target (`imessage`), and expected content keywords. The script calls only:

```text
POST /api/mail/rules/ai/draft
```

It never calls `POST /api/mail/rules`, never saves drafts, never sends iMessage, never calls the bridge, never calls IMAP, and never mutates Gmail. Normal pytest coverage mocks HTTP responses and does not require local Ollama.

Manual run:

```bash
export FINANCE_API_KEY="$(cat secrets/finance_api.key 2>/dev/null || echo "$FINANCE_API_KEY")"
python3 scripts/mail_rule_ai_golden_probe.py \
  --api-base http://127.0.0.1:8090 \
  --fixture agent/tests/fixtures/rule_ai_golden_prompts.json \
  --timeout 120
```

Useful options:

```bash
python3 scripts/mail_rule_ai_golden_probe.py --json
python3 scripts/mail_rule_ai_golden_probe.py --prompt-id bca_suspicious_transaction
python3 scripts/mail_rule_ai_golden_probe.py --fail-fast
```

The probe passes a response only when it has HTTP 200, `status=draft`, `saveable=true`, `safety_status=safe_local_alert_draft`, `match_type=ALL`, `from_domain contains <expected domain>`, at least one subject/body content condition containing an expected keyword, and exactly one `mark_pending_alert` action targeting `imessage` with `stop_processing=false`.

The probe rejects blocked actions including `delete`, `move_to_folder`, `add_label`, `mark_read`, `mark_unread`, `move_to_spam`, `send_imessage`, `forward`, `auto_reply`, `unsubscribe`, `external_webhook`, `route_to_pdf_pipeline`, `skip_ai_inference`, and `stop_processing`.

Run the smoke harness only after intentionally enabling `[mail.rule_ai].enabled=true` and confirming local Ollama is reachable. The recommended model remains `qwen2.5:7b-instruct-q4_K_M`. Keep `[mail.rule_ai].enabled=false` when not actively testing, and keep cloud LLM integration deferred.

## Implemented 4F.1e Dashboard Golden Probe Panel

Phase 4F.1e exposes the golden prompt smoke check in the dashboard as a manual operator panel. The implementation factors shared validation into:

```text
agent/app/rule_ai_golden_probe.py
```

Both the CLI and the dashboard endpoint use the same validation functions. The backend endpoint is:

```text
POST /api/mail/rules/ai/golden-probe
```

Request shape:

```json
{
  "prompt_ids": null,
  "fail_fast": false,
  "timeout_seconds": 120
}
```

Response shape includes `status` (`passed`, `failed`, or `disabled`), summary counts, current local Rule AI provider/model, per-prompt results, warnings, and explicit safety flags showing that no rule was saved, no iMessage was sent, and Gmail/IMAP were not mutated.

If `[mail.rule_ai].enabled=false`, the endpoint returns `status=disabled`, skips all prompts, and warns that local Rule AI should be enabled only when intentionally testing. Disabled mode is a safe-default state.

The dashboard panel lives in Settings next to the AI Rule Builder. It shows:

- safety chips: does not save rules, does not send iMessage, does not mutate Gmail, does not call IMAP
- a manual “Run Golden Probe” button
- disabled/pass/fail summary with provider/model
- a compact result table with prompt ID, expected domain, actual domain, status, and first error

The panel does not expose Save Rule for probe results, does not populate the normal AI Rule Builder draft, and does not call the existing create-rule API. Preflight reports that the CLI and endpoint exist, but it does not run the probe, call Ollama, or call the endpoint. Normal automated tests use fake clients and do not call real Ollama.

## Implemented 4F.1f Save/Preview Compatibility Hardening

Phase 4F.1f verifies the complete safe operator workflow:

```text
AI draft -> human Save Rule -> saved deterministic rule -> local preview/evaluation
```

This phase does not add a new AI capability. It proves that every saveable draft shape from Phase 4F.1a through 4F.1c is compatible with the existing rule-save endpoint, the deterministic rule engine, and the dashboard save path.

Backend compatibility guarantees:

- Sender suppression drafts save through existing `POST /api/mail/rules`.
- Alert-rule drafts save through existing `POST /api/mail/rules`.
- Draft/probe endpoints do not write `mail_rules`, `mail_rule_conditions`, or `mail_rule_actions`.
- Only explicit human Save Rule writes rule rows.
- Saved drafts preview through existing `POST /api/mail/rules/preview`.
- Preview uses synthetic/sample message data and remains local/non-mutating.
- Save endpoint validation still rejects unsupported action types such as direct `send_imessage`.

Field/operator/action compatibility:

- `from_email` is supported as an alias for `sender_email`.
- `from_domain` and `sender_domain` are supported deterministically by deriving the domain from `sender_email` when no explicit domain field exists.
- AI-generated operators remain in the deterministic engine vocabulary (`contains`, `equals` for current drafts).
- AI-generated action types remain in the deterministic engine vocabulary: `skip_ai_inference`, `stop_processing`, and `mark_pending_alert`.

Dashboard save hardening:

- `aiDraftToRuleInput()` emits only the existing `RuleCreate` payload fields: `name`, `account_id`, `match_type`, `conditions`, `actions`, `priority`, and `enabled`.
- It strips draft metadata such as `status`, `saveable`, `safety_status`, `warnings`, `explanation`, `provider`, `model`, and raw model errors.
- It requires `status=draft`, `saveable=true`, a safe local safety status, and human confirmation before returning a payload.
- It avoids duplicate `stop_processing` actions.
- Golden-probe results never expose Save Rule, and unsupported drafts remain unsaveable in the UI helper path.

Safety boundary preserved: no cloud LLM, no auto-save, no bulk creation, no auto-execute, no iMessage at draft/probe/preview time, no bridge call, no Gmail mutation, and no IMAP mutation.

## Implemented 4F.1g Draft Audit Trail And Quality Metrics

Phase 4F.1g adds privacy-conscious local observability for Rule AI draft/probe attempts. It does not expand AI capability and does not change the human-save boundary.

New tables:

```text
mail_rule_ai_draft_audit
mail_rule_ai_golden_probe_runs
```

Draft audit rows are written best-effort after `POST /api/mail/rules/ai/draft` produces a response. The row records status, saveability, safety status, provider/model, request hash, short preview, normalized intent, rule name, condition/action counts, actual sender domain when present, warnings/explanations, and optional `saved_rule_id` linkage when a later human Save Rule request includes `source_draft_audit_id`.

Golden probe runs record aggregate quality metrics: status, total/passed/failed/skipped, provider/model, duration, and compact per-prompt results. They do not store full prompt bodies or raw model output.

Privacy constraints:

- request text is normalized and SHA-256 hashed
- request preview is sanitized and truncated to 160 characters
- raw model responses are not stored
- raw model errors are sanitized/truncated
- API keys, app passwords, bridge tokens, raw email bodies, and mailbox mutation identifiers are not stored

Read-only dashboard/API surface:

```text
GET /api/mail/rules/ai/audit/recent?limit=50&mode=&status=
GET /api/mail/rules/ai/audit/summary
GET /api/mail/rules/ai/golden-probe/runs?limit=20
```

The dashboard **Rule AI Quality** panel shows draft totals, saveable drafts, unsupported/failed counts, saveable rate, latest golden probe summary, and recent draft attempts. It intentionally has no Save Rule, rerun, execute, or mailbox mutation buttons.

Safety boundary preserved: draft endpoint writes audit only and no rule rows; golden probe writes quality-run rows only and no rule rows; only human-triggered `POST /api/mail/rules` writes deterministic rule rows. No iMessage, bridge call, Gmail mutation, IMAP mutation, cloud LLM, bulk creation, or auto-execute is added.

## Phase 4F.2a Deterministic Rule Explanation / Dry-Run Inspector

Phase 4F.2a moves operator trust/debugging from “AI can draft rules safely” to “operator can understand why a saved rule did or did not match.” It does not add new AI drafting behavior.

Endpoint:

```http
POST /api/mail/rules/explain
```

Request:

```json
{
  "message": {
    "sender_email": "alerts@bca.co.id",
    "subject": "Suspicious transaction alert",
    "body_text": "We detected suspicious transaction activity.",
    "imap_account": "gmail_g4ndr1k",
    "imap_folder": "INBOX",
    "has_attachment": false
  },
  "rule_id": null,
  "include_disabled": false
}
```

The endpoint loads saved rules and calls the deterministic rules engine with `preview=True`. It returns per-rule match status, per-condition `field`, `operator`, `expected`, truncated `actual`, `matched`, `case_sensitive`, planned preview-only actions, and aggregate flags such as `would_skip_ai`, `enqueue_ai`, `stopped`, and `route_to_pdf_pipeline`.

`from_domain` / `sender_domain` explanations derive the actual domain from `sender_email` when the payload does not include an explicit domain. For example, `alerts@bca.co.id` explains as actual `bca.co.id`.

Safety boundary preserved: the inspector is read-only. It does not save rules, write draft audit rows, write processing events, write approval rows, call Ollama, call cloud LLMs, call IMAP, call the bridge, send iMessage, mutate Gmail, create labels, move mail, mark read/unread, archive, delete, forward, reply, unsubscribe, or run webhooks. Mutation actions already present on saved rules appear only as dry-run/blocked preview metadata.

The dashboard **Explain Rule** panel accepts synthetic/sample message JSON and can generate a sample from an AI draft, such as `alerts@bca.co.id` for a `from_domain contains bca.co.id` condition. Explanation results intentionally do not show Save Rule or run the golden probe.

## Phase 4F.2b Dashboard E2E Safety Smoke Tests

Phase 4F.2b is test infrastructure only. It adds Playwright browser smoke tests for the dashboard Rule AI safety flows:

```bash
cd mail-dashboard
npm run test:e2e
```

The suite starts the Vite app and mocks every `/api/mail/*` request through Playwright route interception. It does not call real finance-api, Docker, Electron, Ollama, Gmail, IMAP, iMessage, the bridge, a real mailbox, or a real database.

Covered flows:

- saveable sender suppression draft shows Save Rule and sends only normalized RuleCreate payload fields
- unsupported/blocked draft hides Save Rule and does not call `POST /api/mail/rules`
- golden probe disabled and pass/fail states render without Save Rule controls
- Rule AI Quality renders privacy-safe metrics, request previews, and no raw model output/save/rerun/execute controls
- Explain Rule dry-run panel renders matched conditions, expected vs actual values, planned local actions, skip/stop flags, and safety copy

The sender suppression smoke captures the outgoing Save Rule body and verifies draft metadata is stripped before save: no `status`, `saveable`, `safety_status`, warnings, explanation, provider, model, or raw model error.

## Phase 4F.2c Control Center E2E Safety Smoke Tests

Phase 4F.2c is test infrastructure only. It adds Playwright browser smoke tests for the Control Center / Operator Approval safety flows.

The suite continues to start Vite and mocks every `/api/mail/*` request through Playwright route interception. It fails closed on unmocked mail API calls and does not call real finance-api, Docker, Electron, Ollama, Gmail, IMAP, iMessage, the bridge, a real mailbox, or a real database.

Covered Control Center flows:

- synthetic QA mode is read-only and does not call approval, cleanup, or IMAP endpoints
- pending approvals expose approve/reject/expire only, with no mock execute, bulk execute, or auto execute control
- approved approvals expose **Mock verify + audit** only and do not imply Gmail mutation
- blocked terminal approvals show blockers/final verification details and no retry or bulk execute path
- stuck started approvals show manual review and allow only the mocked mark-failed endpoint
- cleanup preview is read-only on page load and cleanup requires explicit confirmation
- Export JSON calls only the mocked export endpoint and does not execute approvals

## 2026-05-01 Validation Checkpoint

Manual validation after schema hardening established the current local-model recommendation for this narrow flow:

- Gemma failed the initial local alert-rule schema probe.
- Qwen initially failed before schema hardening.
- After enabling structured Ollama JSON schema output and keeping deterministic post-validation mandatory, `qwen2.5:7b-instruct-q4_K_M` passed 5/5 manual alert-rule prompts.
- No cloud LLM provider is needed yet for the current narrow Phase 4F.1b scope.

The five manual prompts that passed were:

1. If BCA emails me about suspicious transaction, notify me.
2. If CIMB Niaga asks for credit card transaction confirmation, send me an iMessage notification.
3. Notify me if Maybank sends a security alert.
4. If Permata asks me to confirm a kartu kredit transaction, alert me.
5. If the email is from klikbca about login/security, notify me.

For each passed prompt:

- the endpoint returned HTTP 200
- `status=draft`
- `saveable=true`
- `safety_status=safe_local_alert_draft`
- the rule used only safe condition fields/operators
- the only action was `mark_pending_alert`
- the action target was `imessage`
- no iMessage was sent at draft time
- no Gmail mutation occurred
- no IMAP mutation occurred
- no rule row was saved by the draft endpoint

Decision preserved from this checkpoint:

- continue local-first with `qwen2.5:7b-instruct-q4_K_M` for narrow Phase 4F rule drafting
- keep structured Ollama JSON schema output mandatory where supported
- keep deterministic post-validation mandatory
- keep `[mail.rule_ai].enabled=false` by default unless actively testing
- keep cloud provider integration deferred

Phase 4F.1a does not implement iMessage alert drafting, body/subject matching, `contains_any`, real-mailbox preview, conflict detection, auto-save, or Gmail spam behavior.

## Proposed Backend Shape

Implemented module:

```text
agent/app/rule_ai_builder.py
```

API endpoints:

```text
POST /api/mail/rules/ai/draft
POST /api/mail/rules
```

Later planned endpoints:

```text
POST /api/mail/rules/ai/validate
POST /api/mail/rules/ai/preview
```

Endpoint contracts:

- `/api/mail/rules/ai/draft` returns a proposed rule only.
- `/api/mail/rules/ai/validate` validates and normalizes the proposal.
- `/api/mail/rules/ai/preview` shows what would match and what actions would be planned.
- Existing `/api/mail/rules` saves the rule only after user approval.

The AI must not write directly to `mail_rules`, `mail_rule_conditions`, or `mail_rule_actions`.

## Proposed AI Output Schema

The AI response should use a strict structured output shape and be rejected if it cannot be parsed or validated:

```json
{
  "intent_summary": "Suppress alerts from abcd@efcf.com",
  "confidence": 0.86,
  "rule": {
    "name": "Suppress sender abcd@efcf.com",
    "account_id": null,
    "match_type": "ALL",
    "conditions": [
      {
        "field": "from_email",
        "operator": "equals",
        "value": "abcd@efcf.com"
      }
    ],
    "actions": [
      {
        "action_type": "skip_ai_inference",
        "target": null,
        "stop_processing": false
      },
      {
        "action_type": "stop_processing",
        "target": null,
        "stop_processing": true
      }
    ]
  },
  "explanation": [
    "This rule matches messages from abcd@efcf.com.",
    "It suppresses further processing in the Mail Agent only."
  ],
  "warnings": [
    "This does not move messages to Gmail Spam."
  ],
  "requires_user_confirmation": true
}
```

## Phase 4F.1 Safety Allow-List

Phase 4F.1 may draft only safe non-mutating rule actions.

Allowed actions:

- `mark_pending_alert`
- `skip_ai_inference`
- `add_to_needs_reply`
- `route_to_pdf_pipeline`
- `notify_dashboard`
- `stop_processing` / suppress alert

Blocked or deferred actions:

- `delete`
- `move_to_folder`
- `add_label`
- `mark_read`
- `mark_unread`
- `move_to_spam` / junk mutation
- `auto_reply`
- `forward`
- `unsubscribe`
- `external_webhook`

If the user asks for a blocked action, the builder should produce either:

1. A safe local alternative.
2. A blocked proposal requiring unsupported/deferred capability.

For example, "Add abcd@efcf.com to the spam list" should be interpreted safely as a local Mail Agent suppression rule unless and until live spam mutation is explicitly implemented.

Suggested UI copy:

```text
This will suppress alerts for abcd@efcf.com inside Mail Agent.
It will not move existing or future emails to Gmail Spam.
```

## Example 1: Suppressed Sender

User request:

```text
Add abcd@efcf.com to the spam list
```

AI proposal should be a local suppression or blocked-sender rule, not a Gmail spam mutation:

```json
{
  "name": "Suppress sender abcd@efcf.com",
  "match_type": "ALL",
  "conditions": [
    {
      "field": "from_email",
      "operator": "equals",
      "value": "abcd@efcf.com"
    }
  ],
  "actions": [
    {
      "action_type": "skip_ai_inference"
    },
    {
      "action_type": "stop_processing",
      "stop_processing": true
    }
  ]
}
```

## Example 2: Permata Clarification Alert

User request:

```text
If the mail is from Permata Bank asking for clarification on credit card transaction, send me an iMessage notification
```

Example proposed rule:

```json
{
  "name": "Permata credit card clarification alert",
  "match_type": "ALL",
  "conditions": [
    {
      "field": "from_domain",
      "operator": "contains",
      "value": "permatabank.co.id"
    },
    {
      "field": "subject_or_body",
      "operator": "contains_any",
      "value_json": [
        "clarification",
        "klarifikasi",
        "credit card",
        "kartu kredit",
        "transaction",
        "transaksi"
      ]
    }
  ],
  "actions": [
    {
      "action_type": "mark_pending_alert",
      "target": "imessage",
      "value_json": {
        "template": "Permata credit card clarification email detected."
      }
    }
  ]
}
```

This proposal creates a pending alert rule for the existing safe engine. It must not imply that an iMessage has already been sent while the user is reviewing the draft.

## Dashboard Concept

The dashboard should eventually expose an "AI Rule Builder" box.

User flow:

1. User types a natural-language rule request.
2. AI drafts a rule.
3. Dashboard shows rule name, account scope, conditions, actions, plain-English explanation, warnings, safety status, and preview/diff.
4. User clicks Save Rule.
5. Existing rule engine persists the rule.

Dashboard copy must not say the email action has already happened.

Suggested UI labels:

- `AI Rule Draft`
- `Preview Rule`
- `Save Rule`
- `Blocked Action`
- `Safe Local Suppression`
- `This does not mutate Gmail`

## Validation And Conflict Detection

Phase 4F should eventually validate:

- Allowed condition fields.
- Allowed operators.
- Allowed action types.
- Account scope.
- Priority collisions.
- Duplicate rules.
- Shadowed rules.
- Contradictory rules.
- Unsafe actions.
- Ambiguous natural-language requests.
- Missing sender/domain.
- Unclear target action.

If ambiguous, the AI draft should return a clarification warning instead of guessing dangerously.

## Acceptance Boundary

Phase 4F.1 is complete only if:

- The builder drafts proposed rules but does not save them.
- Human review/save is required.
- Drafts pass deterministic schema validation before preview/save.
- Drafts pass the Phase 4F.1 safety allow-list before save.
- Blocked/deferred Gmail/IMAP mutations are surfaced as warnings.
- Gmail spam/move/label/read/unread mutation remains deferred.
- No implementation path lets AI directly mutate mailboxes or execute actions.
