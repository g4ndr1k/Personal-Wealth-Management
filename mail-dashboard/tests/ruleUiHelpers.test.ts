import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  RULE_ACTIONS,
  actionLabel,
  actionRequiresTarget,
  accountOptionLabel,
  accountScopeLabel,
  aiDraftToRuleInput,
  defaultRuleAccountId,
  syntheticMessageFromAiDraft,
  hasPriorityConflict,
  isMutationAction,
  isSaveableAiDraft,
  reorderPayloadForScope,
  rulePayloadWithAccountScope,
} from '../src/views/ruleUiHelpers.ts';
import {
  approvalBanner,
  approvalStatusLabel,
  capabilitySummary,
  configBlockers,
  dryRunPlanSummary,
  finalVerificationSummary,
  identityBlockers,
} from '../src/views/approvalUiHelpers.ts';
import {
  listSyntheticApprovals,
  syntheticApprovalCleanupPreview,
  syntheticApprovalFixtures,
} from '../src/views/approvalFixtures.ts';

const accounts = [
  {
    id: 'acct_g4ndr1k',
    name: 'g4ndr1k',
    email: 'g4ndr1k@gmail.com',
    provider: 'gmail',
    enabled: true,
    status: 'active',
    last_success_at: null,
    last_error: null,
  },
  {
    id: 'acct_dian',
    name: 'Dian Pratiwi',
    email: 'dian@example.com',
    provider: 'gmail',
    enabled: true,
    status: 'active',
    last_success_at: null,
    last_error: null,
  },
  {
    id: 'acct_disabled',
    name: 'Disabled',
    email: 'disabled@example.com',
    provider: 'gmail',
    enabled: false,
    status: 'active',
    last_success_at: null,
    last_error: null,
  },
];

const baseRule = {
  account_id: 'acct_g4ndr1k',
  name: 'Statements',
  priority: 10,
  enabled: true,
  match_type: 'ALL' as const,
  conditions: [],
  actions: [],
};

const rules = [
  { ...baseRule, rule_id: 1, created_at: '', updated_at: '', account_id: 'acct_g4ndr1k', priority: 10 },
  { ...baseRule, rule_id: 2, created_at: '', updated_at: '', account_id: 'acct_g4ndr1k', priority: 20 },
  { ...baseRule, rule_id: 3, created_at: '', updated_at: '', account_id: 'acct_dian', priority: 10 },
  { ...baseRule, rule_id: 4, created_at: '', updated_at: '', account_id: null, priority: 10 },
  { ...baseRule, rule_id: 5, created_at: '', updated_at: '', account_id: null, priority: 20 },
];

test('create account-scoped rule defaults to the first active account', () => {
  assert.equal(defaultRuleAccountId(accounts), 'acct_g4ndr1k');
  assert.equal(rulePayloadWithAccountScope(baseRule, 'acct_g4ndr1k').account_id, 'acct_g4ndr1k');
});

test('create global rule is explicit', () => {
  assert.equal(rulePayloadWithAccountScope(baseRule, null).account_id, null);
});

test('edit account scope can move between accounts', () => {
  assert.equal(defaultRuleAccountId(accounts, 'acct_dian'), 'acct_dian');
  assert.equal(rulePayloadWithAccountScope(baseRule, 'acct_dian').account_id, 'acct_dian');
});

test('duplicate priority fails only within the same account scope', () => {
  assert.equal(hasPriorityConflict(rules, 'acct_g4ndr1k', 10), true);
  assert.equal(hasPriorityConflict(rules, 'acct_dian', 20), false);
  assert.equal(hasPriorityConflict(rules, null, 20), true);
  assert.equal(hasPriorityConflict(rules, null, 10, 4), false);
});

test('rules list displays account labels correctly', () => {
  assert.equal(accountScopeLabel(null, accounts), 'Global');
  assert.equal(accountScopeLabel('acct_g4ndr1k', accounts), 'g4ndr1k');
  assert.equal(accountScopeLabel('acct_dian', accounts), 'Dian Pratiwi');
  assert.equal(accountOptionLabel(accounts[1]), 'Dian Pratiwi — dian@example.com');
});

test('reorder payload is scoped to one account', () => {
  assert.deepEqual(reorderPayloadForScope(rules, 2, -1), [
    { rule_id: 2, priority: 10 },
    { rule_id: 1, priority: 20 },
  ]);
  assert.deepEqual(reorderPayloadForScope(rules, 5, -1), [
    { rule_id: 5, priority: 10 },
    { rule_id: 4, priority: 20 },
  ]);
});

test('safe mutation actions are exposed with target rules', () => {
  assert.equal(RULE_ACTIONS.includes('move_to_folder' as any), true);
  assert.equal(RULE_ACTIONS.includes('mark_read' as any), true);
  assert.equal(actionLabel('move_to_folder'), 'Move to folder');
  assert.equal(isMutationAction('mark_flagged'), true);
  assert.equal(actionRequiresTarget('move_to_folder'), true);
  assert.equal(actionRequiresTarget('mark_read'), false);
});

test('dangerous actions are not exposed', () => {
  const forbidden = [
    'add_label',
    'send_imessage',
    'delete',
    'expunge',
    'auto_reply',
    'forward',
    'unsubscribe',
    'external_webhook',
  ];
  for (const action of forbidden) {
    assert.equal(RULE_ACTIONS.includes(action as any), false, action);
  }
});

test('AI rule draft converts only safe local suppression drafts to save payloads', () => {
  const draft: any = {
    intent_summary: 'Suppress alerts from abcd@efcf.com',
    confidence: 0.95,
    status: 'draft',
    saveable: true,
    safety_status: 'safe_local_suppression',
    requires_user_confirmation: true,
    provider: 'ollama',
    model: 'qwen2.5:7b-instruct-q4_K_M',
    draft_audit_id: 17,
    explanation: [],
    warnings: ['This does not mutate Gmail'],
    rule: {
      account_id: null,
      name: 'Suppress sender abcd@efcf.com',
      match_type: 'ALL',
      conditions: [{ field: 'from_email', operator: 'equals', value: 'abcd@efcf.com' }],
      actions: [
        { action_type: 'skip_ai_inference', target: null, value_json: null, stop_processing: false },
        { action_type: 'stop_processing', target: null, value_json: null, stop_processing: true },
      ],
    },
  };
  assert.deepEqual(aiDraftToRuleInput(draft, 30), {
    name: draft.rule.name,
    account_id: draft.rule.account_id,
    match_type: draft.rule.match_type,
    conditions: [
      {
        field: 'from_email',
        operator: 'equals',
        value: 'abcd@efcf.com',
        value_json: null,
        case_sensitive: false,
      },
    ],
    actions: draft.rule.actions,
    priority: 30,
    enabled: true,
    source_draft_audit_id: 17,
  });
  assert.equal(aiDraftToRuleInput({ ...draft, safety_status: 'unsupported_live_mailbox_action' }, 30), null);
  assert.equal(aiDraftToRuleInput({ ...draft, status: 'unsupported' }, 30), null);
  assert.equal(aiDraftToRuleInput({ ...draft, saveable: false }, 30), null);
  assert.equal(aiDraftToRuleInput({ ...draft, rule: null }, 30), null);
  assert.equal(isSaveableAiDraft(draft), true);
  assert.equal(isSaveableAiDraft({ ...draft, rule: null }), false);

  const alertDraft: any = {
    ...draft,
    safety_status: 'safe_local_alert_draft',
    rule: {
      account_id: null,
      name: 'Permata credit card clarification alert',
      match_type: 'ALL',
      conditions: [
        { field: 'from_domain', operator: 'contains', value: 'permatabank.co.id' },
        { field: 'subject', operator: 'contains', value: 'clarification' },
      ],
      actions: [
        {
          action_type: 'mark_pending_alert',
          target: 'imessage',
          value_json: { template: 'Permata credit card clarification email detected.' },
          stop_processing: false,
        },
      ],
    },
  };
  assert.equal(isSaveableAiDraft(alertDraft), true);
  assert.deepEqual(aiDraftToRuleInput(alertDraft, 40), {
    name: alertDraft.rule.name,
    account_id: alertDraft.rule.account_id,
    match_type: alertDraft.rule.match_type,
    conditions: [
      {
        field: 'from_domain',
        operator: 'contains',
        value: 'permatabank.co.id',
        value_json: null,
        case_sensitive: false,
      },
      {
        field: 'subject',
        operator: 'contains',
        value: 'clarification',
        value_json: null,
        case_sensitive: false,
      },
    ],
    actions: alertDraft.rule.actions,
    priority: 40,
    enabled: true,
    source_draft_audit_id: 17,
  });

  const noisyDraft: any = {
    ...draft,
    safety_status: 'safe_local_suppression',
    status: 'draft',
    saveable: true,
    rule: {
      ...draft.rule,
      actions: [
        ...draft.rule.actions,
        { action_type: 'stop_processing', target: null, value_json: null, stop_processing: true },
      ],
    },
  };
  const payload = aiDraftToRuleInput(noisyDraft, 50) as any;
  assert.deepEqual(Object.keys(payload).sort(), [
    'account_id',
    'actions',
    'conditions',
    'enabled',
    'match_type',
    'name',
    'priority',
    'source_draft_audit_id',
  ]);
  assert.equal('status' in payload, false);
  assert.equal('saveable' in payload, false);
  assert.equal('safety_status' in payload, false);
  assert.equal('provider' in payload, false);
  assert.equal(payload.actions.filter((action: any) => action.action_type === 'stop_processing').length, 1);
});

test('AI rule builder UI keeps save behind safe draft and existing create rule API', () => {
  const settingsSource = readFileSync(new URL('../src/views/Settings.tsx', import.meta.url), 'utf8');
  const apiSource = readFileSync(new URL('../src/api/mail.tsx', import.meta.url), 'utf8');

  assert.match(settingsSource, /AI Rule Draft/);
  assert.match(settingsSource, /Safe Local Suppression/);
  assert.match(settingsSource, /Safe Local Alert Draft/);
  assert.match(settingsSource, /This does not mutate Gmail/);
  assert.match(settingsSource, /This does not send an iMessage now/);
  assert.match(settingsSource, /Local model could not create a safe draft/);
  assert.match(settingsSource, /setAiRuleDraft\(null\)/);
  assert.match(settingsSource, /createRule\(payload\)/);
  assert.doesNotMatch(settingsSource, /draftRuleWithAi\(payload\)/);
  assert.match(apiSource, /fetchWithAuth\('\/api\/mail\/rules\/ai\/draft'/);
  assert.match(apiSource, /fetchWithAuth\('\/api\/mail\/rules'/);
});

test('Rule AI golden probe UI is manual and draft-only', () => {
  const settingsSource = readFileSync(new URL('../src/views/Settings.tsx', import.meta.url), 'utf8');
  const apiSource = readFileSync(new URL('../src/api/mail.tsx', import.meta.url), 'utf8');

  assert.match(settingsSource, /Rule AI Golden Probe/);
  assert.match(settingsSource, /Manual local-model quality check\. Drafts only\. Saves nothing\./);
  assert.match(settingsSource, /Does not save rules/);
  assert.match(settingsSource, /Does not send iMessage/);
  assert.match(settingsSource, /Does not mutate Gmail/);
  assert.match(settingsSource, /Does not call IMAP/);
  assert.match(settingsSource, /Run Golden Probe/);
  assert.match(settingsSource, /Rule AI golden probe disabled/);
  assert.match(settingsSource, /\[mail\.rule_ai\]\.enabled is false/);
  assert.match(settingsSource, /Rule AI golden probe passed/);
  assert.match(settingsSource, /Rule AI golden probe failed/);
  assert.match(settingsSource, /item\.errors\?\.\[0\]/);
  assert.match(apiSource, /runRuleAiGoldenProbe/);
  assert.match(apiSource, /fetchWithAuth\('\/api\/mail\/rules\/ai\/golden-probe'/);

  const panelSource = settingsSource.slice(settingsSource.indexOf('function RuleAiGoldenProbeCard'));
  assert.doesNotMatch(panelSource, /Save Rule/);
  assert.doesNotMatch(panelSource, /createRule/);
});

test('Rule AI quality panel is read-only and privacy-conscious', () => {
  const settingsSource = readFileSync(new URL('../src/views/Settings.tsx', import.meta.url), 'utf8');
  const apiSource = readFileSync(new URL('../src/api/mail.tsx', import.meta.url), 'utf8');

  assert.match(settingsSource, /Rule AI Quality/);
  assert.match(settingsSource, /Audit stores request hashes and short previews only/);
  assert.match(settingsSource, /It does not store raw model output or save rules/);
  assert.match(settingsSource, /Draft attempts/);
  assert.match(settingsSource, /Saveable drafts/);
  assert.match(settingsSource, /Unsupported\/failed/);
  assert.match(settingsSource, /Recent AI draft attempts/);
  assert.match(settingsSource, /Latest golden probe/);
  assert.match(apiSource, /listRuleAiAudit/);
  assert.match(apiSource, /getRuleAiQualitySummary/);
  assert.match(apiSource, /fetchWithAuth\(`\/api\/mail\/rules\/ai\/audit\/recent/);
  assert.match(apiSource, /fetchWithAuth\('\/api\/mail\/rules\/ai\/audit\/summary'/);

  const panelSource = settingsSource.slice(
    settingsSource.indexOf('function RuleAiQualityCard'),
    settingsSource.indexOf('function DraftInfo'),
  );
  assert.doesNotMatch(panelSource, /Save Rule/);
  assert.doesNotMatch(panelSource, /createRule/);
});

test('Rule explanation UI is dry-run only and has no save control', () => {
  const settingsSource = readFileSync(new URL('../src/views/Settings.tsx', import.meta.url), 'utf8');
  const apiSource = readFileSync(new URL('../src/api/mail.tsx', import.meta.url), 'utf8');

  assert.match(settingsSource, /Explain Rule/);
  assert.match(settingsSource, /Run Dry-Run Explanation/);
  assert.match(settingsSource, /Dry-run only/);
  assert.match(settingsSource, /Does not send iMessage/);
  assert.match(settingsSource, /Does not mutate Gmail/);
  assert.match(settingsSource, /Does not call IMAP/);
  assert.match(settingsSource, /condition\.expected/);
  assert.match(settingsSource, /condition\.actual/);
  assert.match(apiSource, /explainRule/);
  assert.match(apiSource, /fetchWithAuth\('\/api\/mail\/rules\/explain'/);

  const panelSource = settingsSource.slice(
    settingsSource.indexOf('function RuleExplainPanel'),
    settingsSource.indexOf('function PreviewFlag'),
  );
  assert.doesNotMatch(panelSource, /Save Rule/);
  assert.doesNotMatch(panelSource, /createRule/);
});

test('synthetic message builder creates explain samples from AI drafts', () => {
  const draft: any = {
    rule: {
      account_id: 'acct1',
      conditions: [
        { field: 'from_domain', operator: 'contains', value: 'bca.co.id' },
        { field: 'subject', operator: 'contains', value: 'suspicious' },
        { field: 'body', operator: 'contains', value: 'transaction' },
      ],
    },
  };
  assert.deepEqual(syntheticMessageFromAiDraft(draft), {
    sender_email: 'alerts@bca.co.id',
    subject: 'suspicious',
    body_text: 'transaction',
    imap_account: 'acct1',
    imap_folder: 'INBOX',
    has_attachment: false,
  });

  assert.equal(syntheticMessageFromAiDraft({
    rule: {
      account_id: null,
      conditions: [{ field: 'from_email', operator: 'equals', value: 'x@y.com' }],
    },
  } as any).sender_email, 'x@y.com');
});

const approvalBase: any = {
  approval_id: 'appr_1',
  source_type: 'ai_trigger',
  source_id: 'trigger_1',
  message_key: 'msg_1',
  account_id: 'acct',
  folder: 'INBOX',
  uidvalidity: '7',
  imap_uid: 42,
  subject: 'Statement',
  sender: 'bank@example.com',
  received_at: null,
  proposed_action_type: 'mark_read',
  proposed_target: null,
  proposed_value: null,
  reason: null,
  ai_category: null,
  ai_urgency_score: null,
  ai_confidence: null,
  status: 'pending',
  requested_at: '',
  decided_at: null,
  decided_by: null,
  decision_note: null,
  executed_at: null,
  execution_status: null,
  execution_state: 'not_requested',
  execution_result: null,
  created_at: '',
  updated_at: '',
  risk_level: 'safe_reversible',
  current_gate_preview: {
    would_execute_now: false,
    would_be_blocked_now: true,
    gate: 'dry_run',
    reason: 'mail.imap_mutations.dry_run_default=true',
    capability: 'known',
    notes: [],
    mode: 'live',
    mutation_enabled: false,
    dry_run_default: true,
    allow_mark_read: false,
    allow_mark_unread: false,
    allow_add_label: false,
    allow_move_to_folder: false,
    capability_cache: {
      status: 'ok',
      supports_store_flags: true,
      supports_move: true,
      supports_gmail_labels: false,
    },
    dry_run_plan: {
      action_type: 'mark_read',
      operation: String.raw`STORE +FLAGS.SILENT (\Seen)`,
      target: null,
      would_mutate: false,
      rollback_hint: String.raw`mark_unread using STORE -FLAGS.SILENT (\Seen)`,
      safety_gates: [{ gate: 'dry_run_default', status: 'blocked', reason: 'true' }],
    },
  },
};

test('approval helper labels readiness-only dry-run plans safely', () => {
  assert.equal(approvalStatusLabel(approvalBase), 'Dry-run planned');
  const banner = approvalBanner(approvalBase);
  assert.equal(banner.title, 'Dry-run plan only.');
  assert.match(banner.body, /live mutation is disabled/);
});

test('approval helper explains live-disabled approved state', () => {
  const approved = {
    ...approvalBase,
    status: 'approved',
    execution_state: 'not_requested',
    current_gate_preview: { ...approvalBase.current_gate_preview, gate: 'mutation_disabled' },
  };
  assert.equal(approvalStatusLabel(approved), 'Approved but live mutation disabled');
  assert.equal(approvalBanner(approved).body, 'Approval records human intent only. It does not mutate Gmail in this phase.');
});

test('approval helper blocks dangerous wording explicitly', () => {
  const dangerous = {
    ...approvalBase,
    proposed_action_type: 'delete',
    risk_level: 'dangerous_blocked',
    current_gate_preview: { ...approvalBase.current_gate_preview, gate: 'unsupported', reason: 'delete remains blocked in Phase 4D.' },
  };
  assert.equal(approvalStatusLabel(dangerous), 'Dangerous / blocked');
  assert.equal(approvalBanner(dangerous).title, 'Dangerous action blocked.');
});

test('approval helper reports missing capability cache blocker', () => {
  const missing = {
    ...approvalBase,
    current_gate_preview: {
      ...approvalBase.current_gate_preview,
      gate: 'capability_cache_missing',
      capability: 'missing',
      capability_cache: null,
    },
  };
  assert.equal(capabilitySummary(missing).cacheStatus, 'missing');
  assert.equal(capabilitySummary(missing).blocker, 'Blocked because the IMAP capability cache has not been probed.');
  assert.match(approvalBanner(missing).body, /No mailbox change/);
});

test('approval helper reports UIDVALIDITY mismatch blocker', () => {
  const mismatch = {
    ...approvalBase,
    current_gate_preview: {
      ...approvalBase.current_gate_preview,
      gate: 'uidvalidity_mismatch',
      reason: 'cached=99 approval=7',
    },
  };
  assert.deepEqual(identityBlockers(mismatch), ['Blocked because UIDVALIDITY is missing or does not match: cached=99 approval=7.']);
  assert.equal(approvalBanner(mismatch).title, 'Mailbox identity is not safe enough to act on.');
});

test('approval helper exposes would_mutate false in dry-run plan', () => {
  const summary = dryRunPlanSummary(approvalBase);
  assert.equal(summary.operation, String.raw`STORE +FLAGS.SILENT (\Seen)`);
  assert.equal(summary.wouldMutate, 'would_mutate=false');
  assert.equal(summary.rollback, String.raw`mark_unread using STORE -FLAGS.SILENT (\Seen)`);
});

test('approval helper lists config blockers for safe defaults', () => {
  const blockers = configBlockers(approvalBase);
  assert.ok(blockers.includes('Global mailbox mutation is disabled: mail.imap_mutations.enabled=false.'));
  assert.ok(blockers.includes('Dry-run default is enabled: mail.imap_mutations.dry_run_default=true.'));
  assert.ok(blockers.includes('Mark-read actions are disabled: allow_mark_read=false.'));
});

test('approval helper labels mock execution and final verification safely', () => {
  const mocked = {
    ...approvalBase,
    status: 'executed',
    execution_state: 'executed',
    execution_status: 'mock_executed',
    execution_result: {
      execution_mode: 'mock',
      final_verification: {
        status: 'verified',
        safe_to_execute: true,
        blockers: [],
        warnings: [],
        mailbox_identity: { uidvalidity_current: '7' },
        message_identity: { message_id_current: 'm42@example.test' },
        current_flags: { seen: false },
      },
    },
  };
  assert.equal(approvalStatusLabel(mocked), 'Mock execution audited');
  assert.match(approvalBanner(mocked).body, /Gmail was not changed/);
  assert.equal(finalVerificationSummary(mocked).status, 'verified');

  const blocked = {
    ...mocked,
    status: 'blocked',
    execution_state: 'blocked',
    execution_status: 'final_verification_blocked',
    execution_result: {
      execution_mode: 'mock',
      final_verification: {
        status: 'blocked',
        safe_to_execute: false,
        blockers: [{ code: 'uidvalidity_mismatch', message: 'Folder UIDVALIDITY changed after approval.' }],
        warnings: [],
      },
    },
  };
  assert.equal(approvalStatusLabel(blocked), 'Verification blocked');
  assert.match(approvalBanner(blocked).title, /Final read-only verification/);
  assert.deepEqual(finalVerificationSummary(blocked).blockers, ['uidvalidity_mismatch: Folder UIDVALIDITY changed after approval.']);
});

test('synthetic approval fixtures cover representative visual states', () => {
  assert.equal(syntheticApprovalFixtures.length, 10);
  assert.deepEqual(
    syntheticApprovalFixtures.map((approval) => approval.account_id),
    Array(10).fill('fixture.gmail.local'),
  );
  assert.equal(listSyntheticApprovals({ status: 'pending' }).length, 6);
  assert.equal(listSyntheticApprovals({ include_archived: false }).some((approval) => approval.is_archived), false);
  assert.equal(listSyntheticApprovals({ include_archived: true }).some((approval) => approval.is_archived), true);
});

test('synthetic fixtures render safe labels and blockers', () => {
  const byId = Object.fromEntries(syntheticApprovalFixtures.map((approval) => [approval.approval_id, approval]));
  assert.equal(approvalStatusLabel(byId['fixture-readiness-dry-run']), 'Dry-run planned');
  assert.equal(dryRunPlanSummary(byId['fixture-readiness-dry-run']).wouldMutate, 'would_mutate=false');
  assert.equal(approvalStatusLabel(byId['fixture-approved-live-disabled']), 'Approved but live mutation disabled');
  assert.equal(approvalBanner(byId['fixture-approved-live-disabled']).title, 'Approved but live mutation is disabled.');
  assert.equal(approvalStatusLabel(byId['fixture-dangerous-blocked']), 'Dangerous / blocked');
  assert.equal(capabilitySummary(byId['fixture-capability-cache-missing']).blocker, 'Blocked because the IMAP capability cache has not been probed.');
  assert.deepEqual(identityBlockers(byId['fixture-uidvalidity-mismatch']), ['Blocked because UIDVALIDITY is missing or does not match: cached=8123 approval=9001.']);
});

test('synthetic cleanup preview is read-only and non-destructive', () => {
  assert.equal(syntheticApprovalCleanupPreview.cleanup_enabled, false);
  assert.equal(syntheticApprovalCleanupPreview.would_hard_delete, 0);
  assert.match(syntheticApprovalCleanupPreview.notes.join(' '), /No approval rows/);
});
