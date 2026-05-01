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

Phase 4F.1b adds a local Ollama-only capability probe behind `[mail.rule_ai]`. It is disabled by default. The probe drafts alert rules only and is meant to help inspect whether the local model can reliably convert a narrow natural-language request into a safe rule draft.

`POST /api/mail/rules/ai/draft` now accepts:

- `mode=auto`
- `mode=sender_suppression`
- `mode=alert_rule`

Backwards-compatible requests without `mode` still support deterministic 4F.1a sender suppression. `alert_rule` uses the local LLM only when `[mail.rule_ai].enabled=true`; otherwise it returns an unsupported response and no saveable rule.

The only saveable Phase 4F.1b alert shape is:

- `match_type`: `ALL`
- conditions: `from_domain contains <domain>` or `from_email equals <email>`
- content condition: at least one `subject contains <keyword>` or `body contains <keyword>`
- action: `mark_pending_alert`, target `imessage` or another local target, `stop_processing=false`
- `safety_status`: `safe_local_alert_draft`
- `requires_user_confirmation`: `true`

Known bank-domain hints are deterministic and local:

```python
BANK_DOMAIN_HINTS = {
    "permata": "permatabank.co.id",
    "permata bank": "permatabank.co.id",
    "bca": "bca.co.id",
    "klikbca": "klikbca.com",
    "cimb": "cimbniaga.co.id",
    "cimb niaga": "cimbniaga.co.id",
    "maybank": "maybank.co.id",
}
```

For “Permata Bank”, post-validation normalizes the draft domain to `permatabank.co.id`.

The LLM output is post-validated deterministically. Unsupported or unsafe outputs return `status=unsupported`, `saveable=false`, no rule, and short sanitized error text when relevant. The endpoint does not write `mail_rules`, `mail_rule_conditions`, or `mail_rule_actions`; it does not call IMAP, call mailbox execution code, mutate Gmail, call the bridge, send iMessage, auto-save, or auto-execute. If local model quality is insufficient, cloud provider integration may be considered in a separate future phase.
- forward
- reply
- unsubscribe

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
