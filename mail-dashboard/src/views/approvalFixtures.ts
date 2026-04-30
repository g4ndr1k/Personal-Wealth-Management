import type { ApprovalCleanupPreview, MailActionApproval } from '../api/mail';

const account = 'fixture.gmail.local';
const folder = 'INBOX';
const requestedAt = '2026-04-30T09:00:00+07:00';

function event(id: number, approvalId: string, eventType: string, outcome = 'fixture') {
  return {
    id,
    message_id: approvalId,
    account_id: account,
    bridge_id: approvalId,
    rule_id: null,
    action_type: null,
    event_type: eventType,
    outcome,
    details: { synthetic_fixture: true },
    created_at: requestedAt,
  };
}

function dryRunPlan(actionType: string, operation: string, target: string | null, rollbackHint: string) {
  return {
    action_type: actionType,
    operation,
    target,
    would_mutate: false,
    rollback_hint: rollbackHint,
    safety_gates: [
      { gate: 'synthetic_fixture', status: 'info', reason: 'Frontend-only visual QA fixture.' },
      { gate: 'dry_run_default', status: 'blocked', reason: 'mail.imap_mutations.dry_run_default=true' },
      { gate: 'mutation_enabled', status: 'blocked', reason: 'mail.imap_mutations.enabled=false' },
    ],
  };
}

function capabilityCache(overrides: Record<string, any> = {}) {
  return {
    status: 'ok',
    uidvalidity: '9001',
    supports_store_flags: true,
    supports_move: true,
    supports_gmail_labels: true,
    probed_at: requestedAt,
    ...overrides,
  };
}

function fixtureApproval(overrides: Partial<MailActionApproval> & { approval_id: string }): MailActionApproval {
  const actionType = overrides.proposed_action_type || 'mark_read';
  const target = overrides.proposed_target ?? null;
  const base: MailActionApproval = {
    approval_id: overrides.approval_id,
    source_type: 'ai_trigger',
    source_id: 'fixture_trigger',
    message_key: `fixture-message-${overrides.approval_id}`,
    account_id: account,
    folder,
    uidvalidity: '9001',
    imap_uid: 10001,
    subject: 'Synthetic approval visual QA message',
    sender: 'sender@fixture.invalid',
    received_at: requestedAt,
    proposed_action_type: actionType,
    proposed_target: target,
    action_type: actionType,
    target,
    proposed_value: null,
    proposed_value_json: null,
    reason: 'Synthetic visual QA record. No backend row, Gmail account, or IMAP credential is used.',
    ai_category: 'visual_qa',
    ai_urgency_score: 0.42,
    ai_confidence: 0.99,
    status: 'pending',
    requested_at: requestedAt,
    decided_at: null,
    decided_by: null,
    decision_note: null,
    executed_at: null,
    execution_status: null,
    execution_state: 'not_requested',
    execution_result: null,
    created_at: requestedAt,
    updated_at: requestedAt,
    risk_level: 'safe_reversible',
    risk_reasons: ['Synthetic fixture only. Review labels, blockers, and dry-run copy.'],
    reversibility: 'Reversible in a future live phase; no mailbox mutation is available in this fixture.',
    operator_guidance: 'Synthetic visual QA only. Buttons that would call approval endpoints are disabled in fixture mode.',
    preview_title: 'Synthetic visual QA approval',
    preview_summary: 'Frontend-only fixture used to inspect populated Control Center states.',
    would_execute_now: false,
    would_be_blocked_now: true,
    current_gate_preview: {
      would_execute_now: false,
      would_be_blocked_now: true,
      gate: 'dry_run',
      reason: 'Synthetic fixture dry-run plan; mail.imap_mutations.dry_run_default=true.',
      capability: 'known',
      notes: ['Synthetic data; no API write or IMAP action is available.'],
      mode: 'live',
      mutation_enabled: false,
      dry_run_default: true,
      allow_mark_read: false,
      allow_mark_unread: false,
      allow_add_label: false,
      allow_move_to_folder: false,
      identity_complete: true,
      uidvalidity_guard: true,
      capability_cache: capabilityCache(),
      dry_run_plan: dryRunPlan(actionType, 'STORE +FLAGS.SILENT (\\Seen)', target, 'mark_unread using STORE -FLAGS.SILENT (\\Seen)'),
      reversible: true,
      safety_gates: [
        { gate: 'synthetic_fixture', status: 'info', reason: 'Frontend-only fixture.' },
        { gate: 'mutation_enabled', status: 'blocked', reason: 'mail.imap_mutations.enabled=false' },
      ],
    },
    message_context: {
      sender: 'sender@fixture.invalid',
      subject: 'Synthetic approval visual QA message',
      received_at: requestedAt,
      account_id: account,
      account_label: account,
      folder,
      imap_uid: 10001,
      uidvalidity: '9001',
      classification_category: 'visual_qa',
      ai_summary: 'Fake message context for approval UI verification.',
      urgency_score: 0.42,
      confidence: 0.99,
    },
    trigger_context: {
      trigger_id: 'fixture_trigger',
      trigger_name: 'Synthetic visual QA trigger',
      category: 'visual_qa',
      action: { action_type: actionType, target, value: null },
      reason: 'Created locally by the dashboard fixture harness.',
    },
    rule_context: null,
    expires_at: '2026-05-01T09:00:00+07:00',
    approved_at: null,
    rejected_at: null,
    message_id: `fixture-message-${overrides.approval_id}`,
    trigger_id: 'fixture_trigger',
    audit_event_ids: [1],
    events: [event(1, overrides.approval_id, 'synthetic_fixture_loaded')],
  };
  return {
    ...base,
    ...(overrides as MailActionApproval),
    message_context: {
      ...base.message_context,
      ...overrides.message_context,
    },
    current_gate_preview: {
      ...base.current_gate_preview,
      ...overrides.current_gate_preview,
      dry_run_plan: overrides.current_gate_preview?.dry_run_plan ?? base.current_gate_preview?.dry_run_plan,
      capability_cache: overrides.current_gate_preview?.capability_cache ?? base.current_gate_preview?.capability_cache,
    },
    events: overrides.events ?? base.events,
    is_synthetic_fixture: true,
  } as MailActionApproval & { is_synthetic_fixture: true };
}

export const syntheticApprovalFixtures: MailActionApproval[] = [
  fixtureApproval({
    approval_id: 'fixture-readiness-dry-run',
    preview_title: '1. Readiness-only dry-run planned',
    proposed_action_type: 'mark_read',
    action_type: 'mark_read',
    imap_uid: 10001,
  }),
  fixtureApproval({
    approval_id: 'fixture-approved-live-disabled',
    preview_title: '2. Approved but live mutation disabled',
    status: 'approved',
    decided_at: '2026-04-30T09:05:00+07:00',
    approved_at: '2026-04-30T09:05:00+07:00',
    decided_by: 'fixture_operator',
    decision_note: 'Synthetic approval intent only.',
    current_gate_preview: {
      gate: 'mutation_disabled',
      reason: 'mail.imap_mutations.enabled=false',
      dry_run_plan: dryRunPlan('mark_unread', 'STORE -FLAGS.SILENT (\\Seen)', null, 'mark_read using STORE +FLAGS.SILENT (\\Seen)'),
    } as any,
    proposed_action_type: 'mark_unread',
    action_type: 'mark_unread',
    imap_uid: 10002,
  }),
  fixtureApproval({
    approval_id: 'fixture-dangerous-blocked',
    preview_title: '3. Dangerous delete remains blocked',
    status: 'blocked',
    execution_state: 'blocked',
    execution_status: 'blocked',
    blocked_reason: 'delete remains blocked in Phase 4D visual QA.',
    proposed_action_type: 'delete',
    action_type: 'delete',
    risk_level: 'dangerous_blocked',
    risk_reasons: ['Delete is intentionally unsupported and blocked.'],
    current_gate_preview: {
      gate: 'unsupported',
      capability: 'not_applicable',
      reason: 'delete remains blocked in Phase 4D visual QA.',
      dry_run_plan: dryRunPlan('delete', 'UNSUPPORTED delete', null, 'No rollback hint because delete has no live path.'),
    } as any,
  }),
  fixtureApproval({
    approval_id: 'fixture-missing-uid',
    preview_title: '4. Missing UID identity blocker',
    imap_uid: null,
    message_context: { imap_uid: null },
    current_gate_preview: {
      gate: 'identity_incomplete',
      reason: 'UID is missing from the synthetic message identity.',
    } as any,
  }),
  fixtureApproval({
    approval_id: 'fixture-missing-uidvalidity',
    preview_title: '5. Missing UIDVALIDITY blocker',
    uidvalidity: null,
    message_context: { uidvalidity: null },
    current_gate_preview: {
      gate: 'identity_incomplete',
      reason: 'UIDVALIDITY is missing from the synthetic message identity.',
    } as any,
  }),
  fixtureApproval({
    approval_id: 'fixture-uidvalidity-mismatch',
    preview_title: '6. UIDVALIDITY mismatch blocker',
    uidvalidity: '9001',
    message_context: { uidvalidity: '9001' },
    current_gate_preview: {
      gate: 'uidvalidity_mismatch',
      reason: 'cached=8123 approval=9001',
      capability_cache: capabilityCache({ uidvalidity: '8123' }),
    } as any,
  }),
  fixtureApproval({
    approval_id: 'fixture-capability-cache-missing',
    preview_title: '7. Missing capability cache blocker',
    proposed_action_type: 'add_label',
    action_type: 'add_label',
    proposed_target: 'Fixture/Review',
    target: 'Fixture/Review',
    current_gate_preview: {
      gate: 'capability_cache_missing',
      capability: 'missing',
      reason: 'No cached IMAP capability probe exists for fixture.gmail.local / INBOX.',
      capability_cache: null,
      dry_run_plan: dryRunPlan('add_label', 'X-GM-LABELS add', 'Fixture/Review', 'remove label Fixture/Review'),
    } as any,
  }),
  fixtureApproval({
    approval_id: 'fixture-disabled-account',
    preview_title: '8. Disabled account blocker',
    current_gate_preview: {
      gate: 'account_disabled',
      reason: 'fixture.gmail.local is disabled in this synthetic account state.',
    } as any,
  }),
  fixtureApproval({
    approval_id: 'fixture-terminal-archived',
    preview_title: '9. Terminal / archived audited record',
    status: 'expired',
    execution_state: 'expired',
    archived_at: '2026-04-30T10:00:00+07:00',
    is_archived: true,
    current_gate_preview: {
      gate: 'terminal',
      reason: 'Synthetic terminal record archived from active view.',
    } as any,
    events: [
      event(901, 'fixture-terminal-archived', 'approval_expired', 'expired'),
      event(902, 'fixture-terminal-archived', 'approval_archived', 'archived'),
    ],
  }),
  fixtureApproval({
    approval_id: 'fixture-cleanup-non-empty',
    preview_title: '10. Cleanup preview non-empty context',
    status: 'failed',
    execution_state: 'failed',
    execution_status: 'failed',
    execution_error: 'Synthetic terminal failure retained for cleanup preview display.',
    current_gate_preview: {
      gate: 'terminal',
      reason: 'Terminal fixture used with non-empty cleanup preview counts.',
    } as any,
  }),
];

export const syntheticApprovalCleanupPreview: ApprovalCleanupPreview = {
  cleanup_enabled: false,
  would_expire_pending: 2,
  would_archive_terminal: 1,
  would_hard_delete: 0,
  stuck_or_started_excluded: 1,
  auto_expire_pending_after_hours: 24,
  retain_audit_days: 365,
  archive_terminal_after_days: 7,
  examples: {
    expire_pending: syntheticApprovalFixtures.slice(0, 2).map((approval) => ({
      approval_id: approval.approval_id,
      status: approval.status,
      subject: approval.subject,
      account_id: approval.account_id,
      folder: approval.folder,
      requested_at: approval.requested_at,
    })),
    archive_terminal: syntheticApprovalFixtures.slice(-1).map((approval) => ({
      approval_id: approval.approval_id,
      status: approval.status,
      subject: approval.subject,
      account_id: approval.account_id,
      folder: approval.folder,
      requested_at: approval.requested_at,
    })),
  },
  notes: [
    'Synthetic visual QA preview only.',
    'No approval rows are expired, archived, deleted, or written.',
  ],
};

export function listSyntheticApprovals(options: {
  status?: string;
  execution_state?: string;
  risk_level?: string;
  include_archived?: boolean;
} = {}) {
  return syntheticApprovalFixtures.filter((approval) => {
    if (options.status && approval.status !== options.status) return false;
    if (options.execution_state && approval.execution_state !== options.execution_state) return false;
    if (options.risk_level && approval.risk_level !== options.risk_level) return false;
    if (!options.include_archived && approval.is_archived) return false;
    return true;
  });
}
