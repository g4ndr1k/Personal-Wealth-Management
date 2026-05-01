import type { AccountHealth, MailRule, MailRuleInput, RuleAiDraftResult } from '../api/mail';

export const PHASE_4A_SAFE_ACTIONS = [
  'mark_pending_alert',
  'skip_ai_inference',
  'add_to_needs_reply',
  'route_to_pdf_pipeline',
  'notify_dashboard',
  'stop_processing',
] as const;

export const SAFE_MUTATION_ACTIONS = [
  'move_to_folder',
  'mark_read',
  'mark_unread',
  'mark_flagged',
  'unmark_flagged',
] as const;

export const RULE_ACTIONS = [
  ...PHASE_4A_SAFE_ACTIONS,
  ...SAFE_MUTATION_ACTIONS,
] as const;

const ACTION_LABELS: Record<string, string> = {
  mark_pending_alert: 'Mark pending alert',
  skip_ai_inference: 'Skip AI inference',
  add_to_needs_reply: 'Add to needs reply',
  route_to_pdf_pipeline: 'Route to PDF pipeline',
  notify_dashboard: 'Notify dashboard',
  stop_processing: 'Stop processing',
  move_to_folder: 'Move to folder',
  mark_read: 'Mark as read',
  mark_unread: 'Mark as unread',
  mark_flagged: 'Flag',
  unmark_flagged: 'Unflag',
};

export function activeRuleAccounts(accounts: AccountHealth[]) {
  return accounts.filter((account) => account.enabled !== false && account.status === 'active');
}

export function actionLabel(actionType: string) {
  return ACTION_LABELS[actionType] ?? actionType;
}

export function isMutationAction(actionType: string) {
  return SAFE_MUTATION_ACTIONS.includes(actionType as any);
}

export function actionRequiresTarget(actionType: string) {
  return actionType === 'move_to_folder';
}

export function ruleHasMutationAction(rule: MailRule | MailRuleInput) {
  return rule.actions.some((action) => isMutationAction(action.action_type));
}

export function accountOptionLabel(account: AccountHealth) {
  return `${account.name} — ${account.email}`;
}

export function ruleScopeKey(accountId: string | null | undefined) {
  return accountId ?? '__global__';
}

export function defaultRuleAccountId(accounts: AccountHealth[], selectedAccountId?: string | null) {
  const activeAccounts = activeRuleAccounts(accounts);
  if (selectedAccountId && activeAccounts.some((account) => account.id === selectedAccountId)) {
    return selectedAccountId;
  }
  return activeAccounts[0]?.id ?? null;
}

export function accountScopeLabel(accountId: string | null | undefined, accounts: AccountHealth[]) {
  if (accountId == null) return 'Global';
  return accounts.find((account) => account.id === accountId)?.name ?? accountId;
}

export function rulesInScope(rules: MailRule[], accountId: string | null | undefined) {
  const scope = ruleScopeKey(accountId);
  return rules
    .filter((rule) => ruleScopeKey(rule.account_id) === scope)
    .sort((a, b) => a.priority - b.priority || a.rule_id - b.rule_id);
}

export function nextPriorityForScope(rules: MailRule[], accountId: string | null | undefined) {
  const scoped = rulesInScope(rules, accountId);
  const maxPriority = scoped.reduce((max, rule) => Math.max(max, rule.priority), 0);
  return maxPriority + 10;
}

export function hasPriorityConflict(
  rules: MailRule[],
  accountId: string | null | undefined,
  priority: number,
  ignoreRuleId?: number,
) {
  const scope = ruleScopeKey(accountId);
  return rules.some((rule) => (
    rule.rule_id !== ignoreRuleId
    && ruleScopeKey(rule.account_id) === scope
    && rule.priority === priority
  ));
}

export function reorderPayloadForScope(rules: MailRule[], ruleId: number, direction: -1 | 1) {
  const movedRule = rules.find((rule) => rule.rule_id === ruleId);
  if (!movedRule) return [];

  const scoped = rulesInScope(rules, movedRule.account_id);
  const idx = scoped.findIndex((rule) => rule.rule_id === ruleId);
  const swapIdx = idx + direction;
  if (idx < 0 || swapIdx < 0 || swapIdx >= scoped.length) return [];

  const swapped = [...scoped];
  [swapped[idx], swapped[swapIdx]] = [swapped[swapIdx], swapped[idx]];
  return swapped.map((rule, index) => ({
    rule_id: rule.rule_id,
    priority: (index + 1) * 10,
  }));
}

export function rulePayloadWithAccountScope(rule: MailRuleInput, accountId: string | null) {
  return {
    ...rule,
    account_id: accountId,
  };
}

export function aiDraftToRuleInput(
  draft: RuleAiDraftResult,
  priority: number,
): MailRuleInput | null {
  if (!isSaveableAiDraft(draft)) {
    return null;
  }
  const rule = draft.rule!;
  const actions = rule.actions.reduce<MailRuleInput['actions']>((items, action) => {
    if (
      action.action_type === 'stop_processing'
      && items.some((item) => item.action_type === 'stop_processing')
    ) {
      return items;
    }
    items.push({
      action_type: action.action_type,
      target: action.target ?? null,
      value_json: action.value_json ?? null,
      stop_processing: Boolean(action.stop_processing),
    });
    return items;
  }, []);
  return {
    name: rule.name,
    account_id: rule.account_id ?? null,
    match_type: rule.match_type,
    conditions: rule.conditions.map((condition) => ({
      field: condition.field,
      operator: condition.operator,
      value: condition.value ?? null,
      value_json: condition.value_json ?? null,
      case_sensitive: Boolean(condition.case_sensitive),
    })),
    actions,
    priority,
    enabled: true,
    source_draft_audit_id: draft.draft_audit_id ?? null,
  };
}

export function isSaveableAiDraft(draft: RuleAiDraftResult | null) {
  return Boolean(
    draft?.rule
    && draft.status === 'draft'
    && draft.saveable === true
    && (draft.safety_status === 'safe_local_suppression' || draft.safety_status === 'safe_local_alert_draft')
    && draft.requires_user_confirmation === true,
  );
}

export function syntheticMessageFromAiDraft(draft: RuleAiDraftResult | null) {
  const rule = draft?.rule;
  const message: Record<string, any> = {
    sender_email: 'sender@example.com',
    subject: 'Sample message',
    body_text: 'Sample body',
    imap_account: rule?.account_id ?? 'gmail_g4ndr1k',
    imap_folder: 'INBOX',
    has_attachment: false,
  };
  if (!rule) return message;

  for (const condition of rule.conditions) {
    const value = String(condition.value ?? '').trim();
    if (!value) continue;
    if (condition.field === 'from_domain' || condition.field === 'sender_domain') {
      message.sender_email = `alerts@${value.replace(/^@/, '')}`;
    } else if (condition.field === 'from_email' || condition.field === 'sender_email' || condition.field === 'from') {
      message.sender_email = value;
    } else if (condition.field === 'subject') {
      message.subject = value;
    } else if (condition.field === 'body' || condition.field === 'body_text') {
      message.body_text = value;
    }
  }
  return message;
}
