import { expect, test, type Page } from '@playwright/test';

type Approval = Record<string, any>;

const now = '2026-05-02T08:00:00+07:00';

type MailRequest = {
  method: string;
  path: string;
  search: string;
  body: unknown;
};

type ControlCenterMocks = {
  approvals?: Approval[];
  details?: Record<string, Approval>;
  cleanupPreview?: Record<string, any>;
  exportPayload?: Record<string, any>;
};

async function installControlCenterMocks(page: Page, mocks: ControlCenterMocks = {}) {
  const requests: MailRequest[] = [];
  const unmockedRequests: string[] = [];
  const approvals = mocks.approvals ?? [pendingApproval()];
  const details = mocks.details ?? Object.fromEntries(approvals.map((approval) => [approval.approval_id, approval]));
  const cleanupPreview = mocks.cleanupPreview ?? {
    cleanup_enabled: true,
    would_expire_pending: 1,
    would_archive_terminal: 2,
    would_hard_delete: 0,
    stuck_or_started_excluded: 1,
    auto_expire_pending_after_hours: 24,
    retain_audit_days: 365,
    archive_terminal_after_days: 7,
    examples: {},
    notes: ['Preview only.'],
  };

  await page.route('**/api/mail/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    requests.push({ method, path, search: url.search, body });

    const fulfillJson = (responseBody: unknown, status = 200) => route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(responseBody),
    });

    if (method === 'GET' && path === '/api/mail/summary') {
      return fulfillJson({
        total_processed: 0,
        urgent_count: 0,
        drafts_created: 0,
        avg_priority: 0,
        source_split: { gmail: 0, outlook: 0 },
        classification: {},
        actions: {
          drafts_created: 0,
          labels_applied: 0,
          imessage_alerts: 0,
          important_count: 0,
          reply_needed_count: 0,
        },
        mode: 'draft_only',
      });
    }
    if (method === 'GET' && path === '/api/mail/recent') return fulfillJson({ items: [] });
    if (method === 'GET' && path === '/api/mail/accounts') {
      return fulfillJson({ accounts: [{ id: 'gmail_g4ndr1k', name: 'Test Gmail', email: 'test@example.com', provider: 'gmail', enabled: true, status: 'active' }] });
    }

    if (method === 'GET' && path === '/api/mail/approvals/cleanup/preview') return fulfillJson(cleanupPreview);
    if (method === 'POST' && path === '/api/mail/approvals/cleanup') {
      return fulfillJson({ status: 'ok', expired_pending: 1, archived_terminal: 2, hard_deleted: 0 });
    }
    if (method === 'GET' && path === '/api/mail/approvals/export') {
      return fulfillJson(mocks.exportPayload ?? { exported_at: now, approvals, events: [] });
    }
    if (method === 'GET' && path === '/api/mail/approvals') return fulfillJson(approvals);

    const match = path.match(/^\/api\/mail\/approvals\/([^/]+)(?:\/([^/]+))?$/);
    if (match) {
      const approvalId = decodeURIComponent(match[1]);
      const action = match[2];
      const approval = details[approvalId] ?? approvals.find((item) => item.approval_id === approvalId);
      if (!approval) return fulfillJson({ detail: 'approval not found' }, 404);
      if (method === 'GET' && !action) return fulfillJson(approval);
      if (method === 'POST' && action === 'approve') return fulfillJson({ ...approval, status: 'approved', execution_state: 'not_requested', decision_note: body?.decision_note ?? '' });
      if (method === 'POST' && action === 'reject') return fulfillJson({ ...approval, status: 'rejected', execution_state: 'rejected', decision_note: body?.decision_note ?? '' });
      if (method === 'POST' && action === 'expire') return fulfillJson({ ...approval, status: 'expired', execution_state: 'expired' });
      if (method === 'POST' && action === 'execute') {
        return fulfillJson({
          ...approval,
          status: 'executed',
          execution_state: 'executed',
          execution_status: 'executed',
          execution_result: {
            mode: 'mock',
            verification_only: true,
            audit_event_id: 904,
            final_verification: { status: 'passed', safe_to_execute: true, blockers: [], warnings: [] },
          },
          events: [
            ...(approval.events ?? []),
            event(904, approval.approval_id, 'approval_mock_executed', 'mock'),
          ],
        });
      }
      if (method === 'POST' && action === 'mark-failed') return fulfillJson({ ...approval, status: 'failed', execution_state: 'failed', execution_error: body?.reason ?? 'Marked failed' });
      if (method === 'POST' && action === 'archive') return fulfillJson({ ...approval, is_archived: true, archived_at: now });
      if (method === 'POST' && action === 'unarchive') return fulfillJson({ ...approval, is_archived: false, archived_at: null });
    }

    unmockedRequests.push(`${method} ${path}`);
    return fulfillJson({ detail: `Unmocked API request: ${method} ${path}` }, 599);
  });

  return { requests, unmockedRequests };
}

async function openControlCenter(page: Page, options: { synthetic?: boolean } = {}) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Control Center' }).click();
  await expect(page.getByTestId('control-center')).toBeVisible();
  if (options.synthetic === false) {
    const toggle = page.getByRole('button', { name: 'Synthetic QA on' });
    if (await toggle.isVisible()) {
      await toggle.click();
      await expect(page.getByRole('button', { name: 'Synthetic QA off' })).toBeVisible();
    }
  }
  await expect(page.getByText('Loading approvals...')).toHaveCount(0);
}

function mutationCalls(requests: MailRequest[]) {
  return requests.filter((request) => request.method !== 'GET' && request.path.startsWith('/api/mail/approvals'));
}

function mailboxMutationCalls(requests: MailRequest[]) {
  return requests.filter((request) => /imap|gmail|store|move|label|read|unread/i.test(request.path));
}

function expectNoLiveMailboxCalls(api: Awaited<ReturnType<typeof installControlCenterMocks>>) {
  expect(api.unmockedRequests).toEqual([]);
  expect(mailboxMutationCalls(api.requests)).toEqual([]);
}

function baseApproval(overrides: Approval = {}): Approval {
  const approvalId = overrides.approval_id ?? 'approval-pending-1';
  const actionType = overrides.action_type ?? overrides.proposed_action_type ?? 'mark_read';
  return {
    approval_id: approvalId,
    source_type: 'ai_trigger',
    source_id: 'trigger-1',
    message_key: `message-${approvalId}`,
    message_id: `message-${approvalId}`,
    account_id: 'gmail_g4ndr1k',
    folder: 'INBOX',
    uidvalidity: '777',
    imap_uid: 4242,
    subject: 'Bank alert needs review',
    sender: 'alerts@example.test',
    received_at: now,
    proposed_action_type: actionType,
    action_type: actionType,
    proposed_target: overrides.proposed_target ?? null,
    target: overrides.target ?? overrides.proposed_target ?? null,
    proposed_value: overrides.proposed_value ?? null,
    proposed_value_json: overrides.proposed_value_json ?? null,
    reason: 'Matched a local safety test trigger.',
    ai_category: 'finance',
    ai_urgency_score: 0.82,
    ai_confidence: 0.91,
    status: 'pending',
    requested_at: now,
    decided_at: null,
    decided_by: null,
    decision_note: null,
    executed_at: null,
    execution_status: null,
    execution_state: 'not_requested',
    execution_result: null,
    created_at: now,
    updated_at: now,
    risk_level: 'safe_reversible',
    risk_reasons: ['Reversible mailbox flag action, still gated by operator approval.'],
    reversibility: 'Reversible by clearing the mailbox flag in a later live phase.',
    operator_guidance: 'Review the proposed action. Approval records intent only; it does not mutate Gmail in this phase.',
    preview_title: 'Pending approval safety smoke',
    preview_summary: 'Approval fixture for browser smoke tests.',
    would_execute_now: false,
    would_be_blocked_now: true,
    current_gate_preview: {
      would_execute_now: false,
      would_be_blocked_now: true,
      gate: 'mutation_disabled',
      reason: 'mail.imap_mutations.enabled=false',
      capability: 'known',
      notes: ['Mocked browser fixture.'],
      mode: 'live',
      mutation_enabled: false,
      dry_run_default: true,
      allow_mark_read: false,
      allow_mark_unread: false,
      allow_add_label: false,
      allow_move_to_folder: false,
      identity_complete: true,
      uidvalidity_guard: true,
      capability_cache: {
        status: 'ok',
        uidvalidity: '777',
        supports_store_flags: true,
        supports_move: true,
        supports_gmail_labels: true,
        probed_at: now,
      },
      dry_run_plan: {
        action_type: actionType,
        operation: 'STORE +FLAGS.SILENT (\\Seen)',
        target: null,
        would_mutate: false,
        rollback_hint: 'mark_unread using STORE -FLAGS.SILENT (\\Seen)',
        safety_gates: [{ gate: 'mutation_enabled', status: 'blocked', reason: 'mail.imap_mutations.enabled=false' }],
      },
      reversible: true,
      safety_gates: [{ gate: 'mutation_enabled', status: 'blocked', reason: 'mail.imap_mutations.enabled=false' }],
    },
    message_context: {
      sender: 'alerts@example.test',
      subject: 'Bank alert needs review',
      received_at: now,
      account_id: 'gmail_g4ndr1k',
      account_label: 'Test Gmail',
      folder: 'INBOX',
      imap_uid: 4242,
      uidvalidity: '777',
      classification_category: 'finance',
      ai_summary: 'Mocked message context.',
      urgency_score: 0.82,
      confidence: 0.91,
    },
    trigger_context: {
      trigger_id: 'trigger-1',
      trigger_name: 'Safety smoke trigger',
      category: 'finance',
      action: { action_type: actionType, target: null, value: null },
      reason: 'Created by a mocked local trigger.',
    },
    rule_context: null,
    expires_at: '2026-05-03T08:00:00+07:00',
    approved_at: null,
    rejected_at: null,
    trigger_id: 'trigger-1',
    audit_event_ids: [1],
    events: [event(1, approvalId, 'approval_created', 'pending')],
    is_archived: false,
    archived_at: null,
    ...overrides,
  };
}

function pendingApproval() {
  return baseApproval({
    approval_id: 'approval-pending-1',
    preview_title: 'Pending approval safety smoke',
    status: 'pending',
    execution_state: 'not_requested',
    risk_level: 'safe_reversible',
  });
}

function approvedApproval() {
  return baseApproval({
    approval_id: 'approval-approved-1',
    preview_title: 'Approved mock verification smoke',
    status: 'approved',
    execution_state: 'not_requested',
    execution_status: null,
    approved_at: now,
    decided_at: now,
    decided_by: 'operator',
    decision_note: 'Approved for mock verification.',
    operator_guidance: 'Ready for mock verification only. No mailbox mutation is enabled.',
    current_gate_preview: {
      ...baseApproval().current_gate_preview,
      gate: 'ready',
      would_execute_now: true,
      reason: 'Final mock verification may be audited.',
    },
  });
}

function blockedApproval() {
  return baseApproval({
    approval_id: 'approval-blocked-1',
    preview_title: 'Blocked approval safety smoke',
    status: 'blocked',
    execution_state: 'blocked',
    execution_status: 'blocked',
    risk_level: 'dangerous_blocked',
    blocked_reason: 'Live mutation disabled and delete is unsupported.',
    operator_guidance: 'Blocked by safety gate. No mailbox change was made.',
    current_gate_preview: {
      ...baseApproval().current_gate_preview,
      gate: 'mode_blocked',
      would_execute_now: false,
      would_be_blocked_now: true,
      reason: 'mode blocked: live mutation disabled',
      mutation_enabled: false,
      mode: 'blocked',
      dry_run_plan: {
        action_type: 'delete',
        operation: 'UNSUPPORTED delete',
        target: null,
        would_mutate: false,
        rollback_hint: 'No rollback for delete.',
        safety_gates: [{ gate: 'mode_blocked', status: 'blocked', reason: 'Live mutation disabled.' }],
      },
    },
    final_verification: {
      status: 'failed',
      safe_to_execute: false,
      blockers: ['Live mutation disabled', 'Delete unsupported'],
      warnings: ['Manual review required'],
      mailbox: { account_id: 'gmail_g4ndr1k', folder: 'INBOX', uidvalidity: '777' },
      message: { imap_uid: 4242, message_id: 'message-approval-blocked-1' },
      flags: { mutation_enabled: false, mode: 'blocked' },
    },
    execution_result: {
      mode: 'mock',
      final_verification: {
        status: 'failed',
        safe_to_execute: false,
        blockers: ['Live mutation disabled', 'Delete unsupported'],
        warnings: ['Manual review required'],
      },
    },
  });
}

function stuckApproval() {
  return baseApproval({
    approval_id: 'approval-stuck-1',
    preview_title: 'Stuck started approval smoke',
    status: 'approved',
    execution_status: 'started',
    execution_state: 'stuck',
    execution_started_at: '2026-05-02T07:30:00+07:00',
    operator_guidance: 'Execution started but did not finish. Manual review required.',
    execution_error: 'Started execution is stale.',
    current_gate_preview: {
      ...baseApproval().current_gate_preview,
      gate: 'manual_review_required',
      reason: 'Started execution is stale.',
    },
  });
}

function event(id: number, approvalId: string, eventType: string, outcome: string) {
  return {
    id,
    message_id: approvalId,
    account_id: 'gmail_g4ndr1k',
    bridge_id: approvalId,
    rule_id: null,
    action_type: null,
    event_type: eventType,
    outcome,
    details: { mocked_e2e: true },
    created_at: now,
  };
}

test('synthetic QA mode is read-only', async ({ page }) => {
  const api = await installControlCenterMocks(page);
  await openControlCenter(page, { synthetic: true });

  const center = page.getByTestId('control-center');
  await expect(center.getByRole('button', { name: 'Synthetic QA on' })).toBeVisible();
  await expect(center.getByText('Synthetic data only')).toBeVisible();
  await expect(center.getByText('Records are frontend-only fixtures')).toBeVisible();
  await expect(page.getByTestId('approval-row').first()).toBeVisible();
  await expect(center.getByText('Read-only fixture: approval, execution, archive, and cleanup endpoints are unavailable.').first()).toBeVisible();
  await expect(center.getByRole('button', { name: 'Run explicit cleanup' })).toHaveCount(0);

  await page.getByTestId('approval-row').first().click();
  await center.getByRole('button', { name: 'Export JSON' }).click();
  await center.getByRole('button', { name: 'Refresh' }).click();

  expect(mutationCalls(api.requests)).toEqual([]);
  expectNoLiveMailboxCalls(api);
});

test('pending approval shows only approve, reject, and expire controls', async ({ page }) => {
  const pending = pendingApproval();
  const api = await installControlCenterMocks(page, { approvals: [pending] });
  await openControlCenter(page, { synthetic: false });

  const row = page.getByTestId('approval-row').filter({ hasText: 'Pending approval safety smoke' });
  await expect(row.getByText(/^pending$/i).first()).toBeVisible();
  await expect(row.getByText('Safe Reversible')).toBeVisible();
  await expect(row.getByText(/Live mutation disabled|Approval records human intent only/)).toBeVisible();
  await expect(row.getByRole('button', { name: 'Approve attempt' })).toBeVisible();
  await expect(row.getByRole('button', { name: 'Reject' })).toBeVisible();
  await expect(row.getByRole('button', { name: 'Expire' })).toBeVisible();
  await expect(row.getByRole('button', { name: 'Mock verify + audit' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /bulk execute|auto execute/i })).toHaveCount(0);

  await row.getByRole('button', { name: 'Approve attempt' }).click();
  await expect.poll(() => mutationCalls(api.requests).some((request) => request.path === '/api/mail/approvals/approval-pending-1/approve')).toBe(true);
  await row.getByRole('button', { name: 'Reject' }).click();
  await expect.poll(() => mutationCalls(api.requests).some((request) => request.path === '/api/mail/approvals/approval-pending-1/reject')).toBe(true);
  await row.getByRole('button', { name: 'Expire' }).click();
  await expect.poll(() => mutationCalls(api.requests).some((request) => request.path === '/api/mail/approvals/approval-pending-1/expire')).toBe(true);

  expect(mutationCalls(api.requests).map((request) => request.path)).toEqual([
    '/api/mail/approvals/approval-pending-1/approve',
    '/api/mail/approvals/approval-pending-1/reject',
    '/api/mail/approvals/approval-pending-1/expire',
  ]);
  expectNoLiveMailboxCalls(api);
});

test('approved approval exposes mock verification only', async ({ page }) => {
  const approved = approvedApproval();
  const api = await installControlCenterMocks(page, { approvals: [approved] });
  await openControlCenter(page, { synthetic: false });

  const row = page.getByTestId('approval-row').filter({ hasText: 'Approved mock verification smoke' });
  await expect(row.getByText(/^approved$/i).first()).toBeVisible();
  await expect(row.getByRole('button', { name: 'Mock verify + audit' })).toBeVisible();
  await expect(page.getByText(/Execute in Gmail|Move in Gmail|Mark read in Gmail|Live execute/i)).toHaveCount(0);

  await row.getByRole('button', { name: 'Mock verify + audit' }).click();
  await expect.poll(() => mutationCalls(api.requests).some((request) => request.path === '/api/mail/approvals/approval-approved-1/execute')).toBe(true);
  await expect(page.getByTestId('approval-detail').getByText('Mock only')).toBeVisible();
  await expect(page.getByTestId('approval-detail').getByText(/verification|audit/i).first()).toBeVisible();

  expect(mutationCalls(api.requests).map((request) => request.path)).toEqual([
    '/api/mail/approvals/approval-approved-1/execute',
  ]);
  expectNoLiveMailboxCalls(api);
});

test('blocked terminal approval shows blockers and no retry or execute path', async ({ page }) => {
  const blocked = blockedApproval();
  const api = await installControlCenterMocks(page, { approvals: [blocked] });
  await openControlCenter(page, { synthetic: false });

  const row = page.getByTestId('approval-row').filter({ hasText: 'Blocked approval safety smoke' });
  await expect(row.getByText('Blocked').first()).toBeVisible();
  await expect(row.getByText('Live mutation disabled and delete is unsupported.')).toBeVisible();
  await row.click();

  const detail = page.getByTestId('approval-detail');
  await expect(detail.getByText('Manual review required').first()).toBeVisible();
  await expect(detail.getByText('Live mutation disabled and delete is unsupported.')).toBeVisible();
  await expect(detail.getByText('Final verification')).toBeVisible();
  await expect(detail.getByText('failed / safe_to_execute=false')).toBeVisible();
  await expect(detail.getByText('Delete unsupported').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Mock verify + audit' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /retry/i })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /bulk execute/i })).toHaveCount(0);

  expect(mutationCalls(api.requests)).toEqual([]);
  expectNoLiveMailboxCalls(api);
});

test('stuck started approval requires manual review and can be marked failed', async ({ page }) => {
  const stuck = stuckApproval();
  const api = await installControlCenterMocks(page, { approvals: [stuck] });
  await openControlCenter(page, { synthetic: false });

  const row = page.getByTestId('approval-row').filter({ hasText: 'Stuck started approval smoke' });
  await expect(row.getByText(/^stuck$/i).first()).toBeVisible();
  await expect(row.getByText('Manual review required').first()).toBeVisible();
  await row.click();

  const detail = page.getByTestId('approval-detail');
  await expect(detail.getByText('Started execution is stale.').first()).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Mark failed after review' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Mock verify + audit' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /retry/i })).toHaveCount(0);

  await detail.getByRole('button', { name: 'Mark failed after review' }).click();
  await expect.poll(() => mutationCalls(api.requests).some((request) => request.path === '/api/mail/approvals/approval-stuck-1/mark-failed')).toBe(true);
  expect(mutationCalls(api.requests).map((request) => request.path)).toEqual([
    '/api/mail/approvals/approval-stuck-1/mark-failed',
  ]);
  expectNoLiveMailboxCalls(api);
});

test('cleanup preview is read-only and cleanup requires explicit confirmation', async ({ page }) => {
  const api = await installControlCenterMocks(page, { approvals: [pendingApproval()] });
  await openControlCenter(page, { synthetic: false });

  const preview = page.getByTestId('cleanup-preview');
  await expect(preview.getByText('Cleanup preview is read-only')).toBeVisible();
  await expect(preview.getByText('1 pending would expire')).toBeVisible();
  await expect(preview.getByText('2 terminal would archive')).toBeVisible();
  expect(mutationCalls(api.requests).filter((request) => request.path === '/api/mail/approvals/cleanup')).toEqual([]);

  page.on('dialog', async (dialog) => {
    expect(dialog.message()).toContain('Run explicit approval cleanup?');
    await dialog.accept();
  });
  await preview.getByRole('button', { name: 'Run explicit cleanup' }).click();
  await expect.poll(() => mutationCalls(api.requests).some((request) => request.path === '/api/mail/approvals/cleanup')).toBe(true);
  expect(mutationCalls(api.requests).map((request) => request.path)).toEqual([
    '/api/mail/approvals/cleanup',
  ]);
  expectNoLiveMailboxCalls(api);
});

test('export JSON does not execute approvals', async ({ page }) => {
  const api = await installControlCenterMocks(page, { approvals: [approvedApproval()] });
  await openControlCenter(page, { synthetic: false });

  const downloadPromise = page.waitForEvent('download');
  await page.getByTestId('control-center').getByRole('button', { name: 'Export JSON' }).click();
  await downloadPromise;

  await expect.poll(() => api.requests.some((request) => request.method === 'GET' && request.path === '/api/mail/approvals/export')).toBe(true);
  expect(mutationCalls(api.requests)).toEqual([]);
  expect(api.requests.filter((request) => request.path.includes('/execute'))).toEqual([]);
  expect(api.requests.filter((request) => request.path.includes('/cleanup'))).toHaveLength(1);
  expectNoLiveMailboxCalls(api);
});
