import type { MailActionApproval } from '../api/mail';

export const UNSUPPORTED_ACTIONS = new Set([
  'send_imessage',
  'reply',
  'auto_reply',
  'forward',
  'delete',
  'expunge',
  'unsubscribe',
  'webhook',
  'external_webhook',
  'notify_dashboard',
]);

export const READINESS_ACTIONS = new Set(['mark_read', 'mark_unread', 'add_label', 'move_to_folder']);

export function approvalActionType(approval: MailActionApproval) {
  return approval.action_type || approval.proposed_action_type;
}

export function approvalTarget(approval: MailActionApproval) {
  return approval.target || approval.proposed_target;
}

export function humanLabel(value: string | null | undefined) {
  return (value || 'none').replace(/_/g, ' ');
}

export function isReadinessAction(actionType: string) {
  return READINESS_ACTIONS.has(actionType);
}

export function actionSupportLabel(actionType: string) {
  if (UNSUPPORTED_ACTIONS.has(actionType)) return 'Blocked in this phase';
  if (READINESS_ACTIONS.has(actionType)) return 'Readiness-only reversible candidate';
  if (actionType === 'add_to_needs_reply') return 'Operator queue update only';
  return 'Gate checked before any attempt';
}

export function approvalStatusLabel(approval: MailActionApproval) {
  const actionType = approvalActionType(approval);
  const gate = approval.current_gate_preview?.gate;
  const state = approval.execution_state || 'not_requested';
  if (approval.is_archived) return 'Terminal / archived';
  if (approval.execution_status === 'mock_executed') return 'Mock execution audited';
  if (approval.execution_status === 'final_verification_blocked') return 'Verification blocked';
  if (approval.risk_level === 'dangerous_blocked') return 'Dangerous / blocked';
  if (gate === 'unsupported') return 'Unsupported / blocked';
  if (gate === 'dry_run') return 'Dry-run planned';
  if (approval.status === 'approved' && isReadinessAction(actionType)) {
    return 'Approved but live mutation disabled';
  }
  if (isReadinessAction(actionType)) return 'Readiness only';
  if (approval.status === 'pending') return 'Waiting for approval';
  if (['executed', 'blocked', 'failed', 'expired', 'rejected'].includes(state) || ['executed', 'blocked', 'failed', 'expired', 'rejected'].includes(approval.status)) {
    return 'Terminal / audited';
  }
  if (approval.status === 'approved') return 'Approved for one gated attempt';
  return humanLabel(state || approval.status);
}

export function approvalBanner(approval: MailActionApproval) {
  const actionType = approvalActionType(approval);
  const gate = approval.current_gate_preview?.gate;
  const reason = approval.current_gate_preview?.reason || approval.blocked_reason || approval.execution_error;
  const state = approval.execution_state || 'not_requested';

  if (approval.is_archived) {
    return {
      tone: 'neutral',
      title: 'Terminal approval archived from the active view.',
      body: 'Audit history is retained. Archived state does not imply any mailbox change.',
    };
  }
  if (approval.risk_level === 'dangerous_blocked') {
    return {
      tone: 'danger',
      title: 'Dangerous action blocked.',
      body: reason || 'This action remains blocked in this phase and cannot mutate Gmail or any mailbox.',
    };
  }
  if (approval.execution_status === 'final_verification_blocked') {
    const blocker = approval.execution_result?.final_verification?.blockers?.[0];
    return {
      tone: 'danger',
      title: 'Final read-only verification blocked mock execution.',
      body: blocker?.message || 'Mailbox identity changed or could not be proven immediately before mock execution.',
    };
  }
  if (approval.execution_status === 'mock_executed') {
    return {
      tone: 'info',
      title: 'Mock execution audited after read-only verification.',
      body: 'Final verification passed and only a local mock execution record was written. Gmail was not changed.',
    };
  }
  if (gate === 'unsupported') {
    return {
      tone: 'danger',
      title: 'Unsupported action blocked.',
      body: reason || 'No execution path is available for this action in this phase.',
    };
  }
  if (gate === 'uidvalidity_mismatch' || gate === 'identity_incomplete') {
    return {
      tone: 'danger',
      title: 'Mailbox identity is not safe enough to act on.',
      body: reason || 'Blocked because UID, UIDVALIDITY, account, or folder identity is missing or does not match.',
    };
  }
  if (gate === 'capability_cache_missing' || gate === 'capability_unknown') {
    return {
      tone: 'warning',
      title: 'Capability cache is not ready.',
      body: 'Blocked because the IMAP capability cache has not been probed or is unknown. No mailbox change will be made.',
    };
  }
  if (approval.status === 'approved' && isReadinessAction(actionType)) {
    return {
      tone: 'warning',
      title: 'Approved but live mutation is disabled.',
      body: 'Approval records human intent only. It does not mutate Gmail in this phase.',
    };
  }
  if (gate === 'dry_run') {
    return {
      tone: 'info',
      title: 'Dry-run plan only.',
      body: 'This action has a dry-run plan, but live mutation is disabled by configuration or dry-run policy.',
    };
  }
  if (isReadinessAction(actionType)) {
    return {
      tone: 'info',
      title: 'Readiness only - no mailbox change will be made.',
      body: 'The dashboard is showing identity, capability, config, and rollback readiness for a future phase.',
    };
  }
  if (state === 'started' || state === 'stuck') {
    return {
      tone: 'danger',
      title: state === 'stuck' ? 'Started state is stale.' : 'A gated attempt is marked started.',
      body: 'Do not retry automatically. Review the audit trail before marking failed.',
    };
  }
  if (approval.status === 'pending') {
    return {
      tone: 'warning',
      title: 'Waiting for one-person approval.',
      body: 'Approval does not execute anything by itself; the current gates still decide the audited result.',
    };
  }
  return {
    tone: 'neutral',
    title: 'Audit state only.',
    body: 'Review the lifecycle and gate result. Mailbox mutation remains controlled by the configured safety gates.',
  };
}

export function configBlockers(approval: MailActionApproval) {
  const gate = approval.current_gate_preview;
  const actionType = approvalActionType(approval);
  const blockers: string[] = [];
  if (!gate) return ['Gate preview is unavailable from the API.'];
  if (gate.mutation_enabled === false) blockers.push('Global mailbox mutation is disabled: mail.imap_mutations.enabled=false.');
  if (gate.dry_run_default === true) blockers.push('Dry-run default is enabled: mail.imap_mutations.dry_run_default=true.');
  if (gate.allow_mark_read === false) blockers.push('Mark-read actions are disabled: allow_mark_read=false.');
  if (gate.allow_mark_unread === false) blockers.push('Mark-unread actions are disabled: allow_mark_unread=false.');
  if (gate.allow_add_label === false) blockers.push('Label actions are disabled: allow_add_label=false.');
  if (gate.allow_move_to_folder === false) blockers.push('Move-to-folder actions are disabled: allow_move_to_folder=false.');
  if (gate.gate === 'action_not_allowed') blockers.push(`${humanLabel(actionType)} is blocked by its per-action allow flag.`);
  return blockers.length ? blockers : ['No config blocker was reported, but this phase still treats reversible mailbox actions as readiness-only.'];
}

export function identityBlockers(approval: MailActionApproval) {
  const message = approval.message_context || {};
  const gate = approval.current_gate_preview;
  const blockers: string[] = [];
  if (!(message.account_id || approval.account_id || message.account_label)) blockers.push('Missing account identity.');
  if (!(message.folder || approval.folder)) blockers.push('Missing folder.');
  if ((message.imap_uid ?? approval.imap_uid) == null) blockers.push('Missing UID.');
  if ((message.uidvalidity ?? approval.uidvalidity) == null || `${message.uidvalidity ?? approval.uidvalidity}` === '') blockers.push('Missing UIDVALIDITY.');
  if (gate?.gate === 'uidvalidity_mismatch') blockers.push(`Blocked because UIDVALIDITY is missing or does not match: ${gate.reason || 'mismatch reported'}.`);
  if (gate?.gate === 'account_disabled') blockers.push('The configured mail account is disabled or unavailable.');
  if (gate?.gate === 'folder_state_unavailable') blockers.push('Folder state is missing, so UIDVALIDITY cannot be trusted.');
  return blockers;
}

export function capabilitySummary(approval: MailActionApproval) {
  const gate = approval.current_gate_preview;
  const cache = gate?.capability_cache;
  const actionType = approvalActionType(approval);
  const capability = gate?.capability || 'not_applicable';
  const cacheStatus = !cache ? 'missing' : cache.status && cache.status !== 'ok' ? 'unknown' : 'present';
  const supported: string[] = [];
  if (cache?.supports_store_flags === true) supported.push('read/unread flags');
  if (cache?.supports_move === true) supported.push('folder move');
  if (cache?.supports_gmail_labels === true) supported.push('Gmail labels');
  return {
    cacheStatus,
    capability,
    operation: actionType === 'mark_read' || actionType === 'mark_unread'
      ? 'Requires IMAP STORE flag support.'
      : actionType === 'move_to_folder'
        ? 'Requires UID MOVE support.'
        : actionType === 'add_label'
          ? 'Requires Gmail label capability.'
          : 'No IMAP capability is required for this action.',
    supported: supported.length ? supported.join(', ') : 'No supported mailbox operation was reported.',
    blocker: gate?.gate === 'capability_cache_missing'
      ? 'Blocked because the IMAP capability cache has not been probed.'
      : gate?.gate === 'capability_unknown'
        ? 'Blocked because the cached capability result is unknown or stale.'
        : null,
  };
}

export function dryRunPlanSummary(approval: MailActionApproval) {
  const plan = approval.current_gate_preview?.dry_run_plan || approval.current_gate_preview?.mutation_plan;
  if (!plan) {
    return {
      present: false,
      operation: 'No dry-run plan was returned for this approval.',
      target: 'n/a',
      wouldMutate: 'n/a',
      rollback: 'No rollback hint was returned.',
      gates: [],
    };
  }
  return {
    present: true,
    operation: plan.operation || humanLabel(plan.action_type || approvalActionType(approval)),
    target: plan.target || approvalTarget(approval) || 'n/a',
    wouldMutate: plan.would_mutate === false ? 'would_mutate=false' : `would_mutate=${String(plan.would_mutate)}`,
    rollback: plan.rollback_hint || approval.current_gate_preview?.rollback_hint || 'No rollback hint was returned.',
    gates: Array.isArray(plan.safety_gates) ? plan.safety_gates : approval.current_gate_preview?.safety_gates || [],
  };
}

export function finalVerificationSummary(approval: MailActionApproval) {
  const verification = approval.final_verification || approval.execution_result?.final_verification;
  if (!verification) {
    return {
      present: false,
      status: 'not available',
      safe: 'n/a',
      blockers: [],
      warnings: [],
      mailbox: null,
      message: null,
      flags: null,
    };
  }
  return {
    present: true,
    status: verification.status || 'unknown',
    safe: verification.safe_to_execute === true ? 'true' : 'false',
    blockers: Array.isArray(verification.blockers)
      ? verification.blockers.map((blocker: any) => `${blocker.code}: ${blocker.message}`)
      : [],
    warnings: Array.isArray(verification.warnings) ? verification.warnings : [],
    mailbox: verification.mailbox_identity || null,
    message: verification.message_identity || null,
    flags: verification.current_flags || null,
  };
}
