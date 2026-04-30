# Phase 4E Live Reversible IMAP Mutation Design

Design status: Phase 4E design review is complete; Phase 4E.1 now implements only the non-mutating execution foundation. No live mailbox mutation is enabled or implemented by this phase.

Implementation note for 4E.1:

- `mail_action_executions` and `mail_action_execution_events` exist as metadata-only audit tables.
- The gate evaluator, plan hash helper, idempotency helper, and mock executor are implemented for test/dev execution chassis validation.
- Phase 4E.2 adds final read-only verification before mock execution. Verification selects the folder read-only, confirms UIDVALIDITY, fetches the UID/header identity/flags read-only, and blocks mock execution if identity changed.
- The mock executor records `mock_executed` rows only after verification passes and does not touch Gmail or issue IMAP `STORE`, `MOVE`, `COPY`, or `EXPUNGE`.
- `mark_read` and `mark_unread` can reach gate readiness only under explicit future-live-style inputs, but they are still not live actions in 4E.2. Live read/unread remains deferred to Phase 4E.3.
- `add_label`, `move_to_folder`, and dangerous actions remain blocked.
- Phase 4F natural-language rule authoring may happen before Phase 4E.3 because it is non-mutating: AI drafts proposed deterministic rules, deterministic validation checks them, humans approve/save them, and the existing deterministic rules engine applies saved rules later. See [phase-4f-natural-language-rule-builder.md](phase-4f-natural-language-rule-builder.md).

## Scope And Safety Boundary

Phase 4E designs a future execution contract for explicit, human-triggered, reversible IMAP mutations. It preserves the Phase 4D boundary:

- No live mutation by default.
- No autonomous execution.
- No auto-execute after approval.
- No bulk approval or bulk execution.
- No auto-retry.
- No dangerous actions.
- No delete, permanent delete, spam, expunge, forward, auto-reply, unsubscribe, external webhook, or scheduled mutation.
- No implicit label or folder creation.
- No mutation if mailbox identity verification changes.
- No mutation if rollback cannot be safely described before execution.

The recommended first live phase is `mark_read` and `mark_unread` only. `add_label` should remain readiness-only until Gmail label semantics are covered by focused mocks and operator UX. `move_to_folder` should be deferred to a separate review because rollback depends on post-move identity and provider-specific behavior.

## Execution Contract

A future approved action becomes executable only when every gate below passes at execution time. Approval records human intent; it is not execution authorization by itself.

Required gates:

| Gate | Requirement | Failure result |
|---|---|---|
| Agent mode | Agent mode is `live`. | Block execution. |
| Global mutation flag | `[mail.imap_mutations].enabled=true`. | Block execution. |
| Dry-run flag | `[mail.imap_mutations].dry_run_default=false` for live execution. | Return dry-run/blocked, no mutation. |
| Per-action allow flag | Matching allow flag is true, such as `allow_mark_read=true`. | Block execution. |
| Approval status | `mail_action_approvals.status='approved'`. | Block execution. |
| Approval expiry | Approval is not expired under `[mail.approvals].approval_expiry_hours`. | Block execution and mark/return expired according to the existing approval lifecycle. |
| Approval archive state | `archived_at IS NULL`. | Block execution. Archived records are history only. |
| Action reversibility | Action is one of the current reversible candidates and has a concrete rollback plan. | Block execution. |
| Dangerous action check | Action is not in any dangerous or unsupported action set. | Block execution. |
| Account | Referenced account exists and is enabled. | Block execution. |
| Folder state | Stored folder state exists for `(account_id, folder)`. | Block execution. |
| UID | Approval has `imap_uid`. | Block execution. |
| UIDVALIDITY | Approval has `uidvalidity`. | Block execution. |
| UIDVALIDITY match | Current folder state UIDVALIDITY matches the approval UIDVALIDITY. | Block execution. |
| Capability cache | Cache exists when `require_capability_cache=true`. | Block execution. |
| Capability support | Cache supports the required operation. | Block execution. |
| Final verification | Live execution performs a fresh read-only identity/capability check immediately before mutation. | Block execution if it differs from the plan identity. |
| Plan identity | Dry-run plan was generated from the same approval/action context: approval id, account id, folder, UIDVALIDITY, UID, operation, target, and relevant config gates. | Block execution. |
| Plan hash | Stored plan hash matches the current recomputed plan hash. | Block execution. |
| Idempotency | Idempotency key has no completed, started, or uncertain execution record. | Return existing execution state; do not mutate. |

The final verification step should re-select the folder, confirm UIDVALIDITY, confirm the UID is still present, refresh or re-probe required capabilities read-only, and compare those values with the approved plan identity. If anything differs, no mutation occurs.

## Idempotency

The live execution path needs a dedicated execution record keyed by the unique mutation intent, separate from the approval row. The approval row may summarize the latest terminal outcome, but it should not be the only idempotency guard.

Recommended idempotency key fields:

```text
version
approved_action_id
account_id
folder
uidvalidity
uid
operation
target
plan_hash
```

Recommended key:

```text
sha256("imap-mutation:v1|{approval_id}|{account_id}|{folder}|{uidvalidity}|{uid}|{operation}|{target}|{plan_hash}")
```

Rules:

- One execution record exists per unique mutation intent.
- Creating the execution record is atomic and happens before any IMAP mutation.
- A duplicate execute click that finds `started`, `succeeded`, `failed`, or `uncertain` returns that existing execution state and does not call IMAP again.
- A dashboard/API restart reads the durable execution record and continues to report the last known state.
- A connection drop after the command is sent but before the response is known becomes `uncertain`, not retryable. Operator review is required.
- A stuck `started` record remains manual-review only. There is no automatic retry.
- A new approval for the same message/action must still derive a different approval id and should be blocked if a previous equivalent intent is already `succeeded`, `started`, or `uncertain`.

## Audit Trail

The audit trail should be metadata-only. Do not store message body in new mutation audit tables.

Recommended tables:

### `mail_action_executions`

One durable row per unique mutation intent.

Suggested columns:

- `execution_id TEXT PRIMARY KEY`
- `approval_id TEXT NOT NULL`
- `idempotency_key TEXT NOT NULL UNIQUE`
- `plan_hash TEXT NOT NULL`
- `account_id TEXT NOT NULL`
- `folder TEXT NOT NULL`
- `uidvalidity TEXT NOT NULL`
- `imap_uid INTEGER NOT NULL`
- `operation TEXT NOT NULL`
- `target TEXT`
- `approved_by TEXT`
- `approved_at TEXT`
- `execution_requested_at TEXT NOT NULL`
- `execution_started_at TEXT`
- `execution_finished_at TEXT`
- `execution_status TEXT NOT NULL`
- `before_state_json TEXT`
- `after_state_json TEXT`
- `rollback_plan_json TEXT`
- `rollback_status TEXT`
- `imap_response_summary TEXT`
- `error_message TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Suggested statuses:

- `requested`
- `started`
- `succeeded`
- `failed`
- `blocked`
- `uncertain`

### `mail_action_execution_events`

Immutable chronological rows for proof and operator review.

Suggested columns:

- `event_id INTEGER PRIMARY KEY AUTOINCREMENT`
- `execution_id TEXT NOT NULL`
- `approval_id TEXT NOT NULL`
- `event_type TEXT NOT NULL`
- `event_status TEXT NOT NULL`
- `details_json TEXT`
- `created_at TEXT NOT NULL`

Useful event types:

- `execution_requested`
- `idempotency_reserved`
- `final_verification_started`
- `final_verification_passed`
- `final_verification_blocked`
- `imap_command_started`
- `imap_command_succeeded`
- `imap_command_failed`
- `imap_command_uncertain`
- `rollback_planned`
- `rollback_requested`
- `rollback_succeeded`
- `rollback_failed`

The existing `mail_action_approvals` fields can remain the UI summary surface: `decided_by`, `decided_at`, `execution_started_at`, `executed_at`, `execution_status`, and `execution_result_json`.

## Rollback Semantics

Rollback must be representable before mutation. If before-state is unknown, live execution is refused.

| Action | Required before-state | Execution | Rollback |
|---|---|---|---|
| `mark_read` | Current `\Seen` state. | Add `\Seen` only if UID identity is verified. | If original state was unread, remove `\Seen`. If already read, rollback is no-op. |
| `mark_unread` | Current `\Seen` state. | Remove `\Seen` only if UID identity is verified. | If original state was read, add `\Seen`. If already unread, rollback is no-op. |
| `add_label` | Existing labels and proof that target label already exists. | Add existing Gmail user label only. | Remove target label only if the label was not originally present. |
| `move_to_folder` | Original folder, target folder, provider identity model, and post-move identity strategy. | Move only if UID identity and rollback identity can be represented safely. | Move back to original folder only if the message can be safely re-identified and the original folder is known. |

`move_to_folder` should not be included in the first live phase. UID changes after move/copy, Gmail's label/folder model, All Mail behavior, Trash semantics, and archive ambiguity make rollback too easy to overstate.

## Gmail And IMAP Semantics

Gmail v1 safety recommendations:

- `mark_read` and `mark_unread` map to IMAP `\Seen` with `UID STORE +FLAGS.SILENT (\Seen)` and `UID STORE -FLAGS.SILENT (\Seen)`.
- Before-state must be fetched read-only immediately before mutation.
- `add_label` must use Gmail label support, such as `X-GM-LABELS`, and must not create labels implicitly.
- Target labels must be existing user labels, not guessed folders.
- System labels need explicit handling and should be blocked by default.
- `move_to_folder` is deferred because Gmail labels are not traditional folders and "Archive" usually means removing `\Inbox`, not moving to a stable folder.
- All Mail, Trash, Spam, and archive-like behavior are out of scope for v1 live execution.
- `MOVE` support must be preferred over `COPY + STORE + EXPUNGE`; copy/delete fallback remains disabled unless separately reviewed.
- `COPY + STORE + EXPUNGE` is not acceptable for the first live phase because expunge/delete-like semantics complicate rollback and uncertainty handling.
- UID stability cannot be assumed after move or copy. Any action that changes UID must define how the message is re-identified before rollback is offered.

Safe for Gmail v1:

- `mark_read`
- `mark_unread`

Deferred:

- `add_label`
- `remove_label`
- `move_to_folder`
- archive-modeled-as-move
- Trash/Spam/system labels

## Capability Cache And Final Verification

Readiness cache is advisory. Live execution must perform a final read-only verification before mutation.

Required capabilities by operation:

| Action | Capability |
|---|---|
| `mark_read` | `STORE` / UID flag mutation support for `\Seen`. |
| `mark_unread` | `STORE` / UID flag mutation support for `\Seen`. |
| `add_label` | Gmail label support and existing-label verification. |
| `move_to_folder` | `UID MOVE`; copy/delete fallback is not allowed for first live phases. |

Cache policy:

- Readiness display may use cached capabilities.
- Live execution requires a fresh read-only verify step regardless of cache age.
- A cache older than a conservative TTL, such as 15 minutes, should be treated as stale for execute-button readiness.
- If the cache changes between readiness and execution, final verification wins.
- If final verification reports missing, stale, unsupported, or changed capability, no mutation occurs.
- The final verification should update capability cache metadata, but the update itself must not mark an execution as started unless the idempotency record already exists.

## Human Approval Model

Operator flow:

1. AI or deterministic rule suggests an action.
2. Dashboard shows a dry-run plan and readiness gates.
3. Human approves the action. Approval records intent only.
4. A future Execute button appears only when live mutation config is enabled and the static preview has no blockers.
5. Execute performs idempotency reservation and final read-only verification.
6. If verification passes, exactly one IMAP mutation attempt is made.
7. Execution writes immutable audit events and updates the approval summary.
8. A rollback action may be offered only for successful reversible actions with a safe rollback plan and known before-state.

Clarifications:

- Approval is not execution.
- Execution is still explicit.
- No background execution starts after approval.
- No bulk approve or bulk execute exists.
- Rejected, expired, archived, failed, blocked, stuck, uncertain, or already executed records cannot be executed again.

## Failure Modes

| Failure | Expected behavior |
|---|---|
| UID missing | Block before idempotency reservation if possible; otherwise mark execution `blocked`. |
| UIDVALIDITY missing | Block. |
| UIDVALIDITY mismatch | Block; write verification-blocked audit event. |
| Account disabled | Block. |
| Folder missing | Block. |
| Capability cache missing | Block static readiness when required; live final verification may refresh read-only, then block if still missing/unsupported. |
| Capability cache stale | Disable execute readiness or force final re-probe; no mutation if final check differs. |
| Final verification mismatch | Block and record the mismatched field. |
| IMAP connection failure before command | Mark `failed`; no mutation attempted. |
| Server says operation succeeded | Mark `succeeded`, store response summary and after-state. |
| Connection drops after command sent | Mark `uncertain`; no retry. |
| Operation partially succeeds | Mark `uncertain` or `failed` based on read-only after-state; no automatic compensating action. |
| Rollback fails | Mark rollback `failed`, keep original execution `succeeded`, and surface manual review. |
| Dashboard reload during execution | UI reads execution record and shows `started`, `stuck`, `succeeded`, `failed`, or `uncertain`. |
| Duplicate execute request | Return existing execution record; do not mutate again. |

## Future API Contract

Recommended additions for later coding phases:

- `POST /api/mail/approvals/{approval_id}/execute-live`
  - Performs idempotency reservation, final verification, one mutation attempt, and audit writes.
  - May reuse the current execute route only if route semantics remain explicit and dry-run-safe.
- `GET /api/mail/approvals/{approval_id}/executions`
  - Lists execution attempts and immutable events.
- `POST /api/mail/executions/{execution_id}/rollback`
  - Offered only for successful executions with a safe rollback plan.

Response shape should include:

- `execution_id`
- `idempotency_key`
- `execution_status`
- `operation`
- `target`
- `before_state`
- `after_state`
- `rollback_plan`
- `rollback_status`
- `gate_results`
- `imap_response_summary`
- `error_message`

## Test Plan For Later Implementation

Required tests:

- Unit tests for every execution gate.
- Approval expired tests.
- Archived approval tests.
- Dangerous/unsupported action tests.
- Per-action allow-flag tests.
- UID missing and UIDVALIDITY missing tests.
- UIDVALIDITY mismatch tests.
- Folder state missing tests.
- Account disabled tests.
- Capability missing, unsupported, unknown, and stale tests.
- Final verification mismatch tests.
- Plan hash mismatch tests.
- Idempotency key shape tests.
- Duplicate execute click tests.
- API restart idempotency tests using persisted SQLite rows.
- Started/stuck execution tests with no retry.
- Partial execution uncertainty tests.
- Mocked IMAP success/failure tests for `mark_read`.
- Mocked IMAP success/failure tests for `mark_unread`.
- Rollback plan generation tests.
- Rollback refused when before-state is unknown.
- Rollback no-op tests when state already matched before execution.
- Gmail label semantics tests with mocks only.
- Tests proving no live Gmail integration test runs by default.

Suggested non-default manual tests:

- A throwaway mailbox account with `mark_read` and `mark_unread` only.
- No labels, moves, archive, Trash, Spam, or delete-like actions.
- Manual operator checklist requiring backup/export of approval audit before enabling live config.

## Phased Implementation Recommendation

Recommended split:

1. `4E.1`: Schema, execution contract, plan hashing, immutable audit events, and mocked executor only.
2. `4E.2`: Final read-only verification before mock execution, with no live Gmail/IMAP mutation.
3. `4E.3`: Future live `mark_read` / `mark_unread` behind config with explicit human execute only.
4. `4E.4`: Rollback UI for successful `mark_read` / `mark_unread`.
5. `4E.5`: Gmail `add_label` only after existing-label verification, system-label blocking, and mock coverage are solid.
6. `4E.6`: `move_to_folder` only after separate design review, or defer indefinitely.

Explicit recommendation: do not implement `move_to_folder` now. Defer it until rollback identity is airtight and Gmail folder/label behavior has its own reviewed contract.

## Remaining Open Questions

- Should the current `execute` route remain the future live endpoint, or should live mutation use a new route name to make operator intent clearer?
- What TTL should define stale capability cache in the dashboard: 5, 15, or 60 minutes?
- Should final verification always update the capability cache, or only when the probe succeeds?
- Who is the canonical `approved_by` identity in local/private deployments: API key label, local operator name, dashboard profile, or config value?
- Should an equivalent previous successful intent block a new approval forever, or only while the message still has matching UID/UIDVALIDITY identity?
- What metadata can safely identify a moved Gmail message for rollback without storing body content?
- Should rollback itself require a second human approval, or is an explicit rollback click enough for the same operator session?
