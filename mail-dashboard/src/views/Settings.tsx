import { ReactNode, useEffect, useState } from 'react';
import {
  ApiError,
  AiClassification,
  AiSettings,
  AiTrigger,
  AiTriggerCondition,
  AiTriggerInput,
  AiTriggerPreviewResult,
  MailProcessingEvent,
  MailRule,
  MailRuleAction,
  MailRuleCondition,
  MailRuleInput,
  RuleAiDraftResult,
  RuleAiAuditItem,
  RuleAiGoldenProbeResponse,
  RuleAiQualitySummary,
  RuleExplainResponse,
  RulePreviewResult,
  useApi,
} from '../api/mail';
import {
  RULE_ACTIONS,
  actionLabel,
  actionRequiresTarget,
  accountOptionLabel,
  accountScopeLabel,
  activeRuleAccounts,
  aiDraftToRuleInput,
  defaultRuleAccountId,
  hasPriorityConflict,
  isMutationAction,
  isSaveableAiDraft,
  nextPriorityForScope,
  reorderPayloadForScope,
  ruleHasMutationAction,
  rulesInScope,
  syntheticMessageFromAiDraft,
} from './ruleUiHelpers';

function normalizeAppPassword(value: string) {
  return value.replace(/\s+/g, '');
}

const SAFE_ACTIONS = RULE_ACTIONS;

const OPERATORS = [
  'equals',
  'contains',
  'starts_with',
  'ends_with',
  'regex',
  'exists',
  'domain_equals',
  'in',
];

const FIELDS = [
  'sender_email',
  'subject',
  'body_text',
  'from',
  'sender',
  'folder',
  'account',
  'has_attachment',
];

const emptyCondition = (): MailRuleCondition => ({
  field: 'sender_email',
  operator: 'contains',
  value: '',
  value_json: null,
  case_sensitive: false,
});

const emptyAction = (): MailRuleAction => ({
  action_type: 'notify_dashboard',
  target: '',
  value_json: null,
  stop_processing: false,
});

const emptyAiTriggerCondition = (): AiTriggerCondition => ({
  field: 'category',
  operator: 'equals',
  value: 'payment_due',
});

const newAiTriggerDraft = (order = 1): AiTriggerInput => ({
  name: 'New AI trigger',
  enabled: true,
  priority: order * 10,
  conditions_json: {
    match_type: 'ALL',
    conditions: [
      emptyAiTriggerCondition(),
      { field: 'urgency_score', operator: '>=', value: 7 },
    ],
  },
  actions_json: [{ action_type: 'notify_dashboard' }],
  cooldown_seconds: 3600,
});

const newRuleDraft = (priority: number, accountId: string | null): MailRuleInput => ({
  account_id: accountId,
  name: 'New rule',
  priority,
  enabled: true,
  match_type: 'ALL',
  conditions: [emptyCondition()],
  actions: [emptyAction()],
});

const samplePreview = {
  message_key: 'preview-message',
  message_id: '<preview@example.local>',
  imap_account: 'gmail_g4ndr1k',
  imap_folder: 'INBOX',
  sender_email: 'billing@example.com',
  subject: 'Invoice for review',
  body_text: 'Please review this statement.',
  attachments: [],
};

export default function Settings() {
  const {
    accounts,
    error,
    testAccount,
    addAccount,
    deleteAccount,
    reactivateAccount,
    reloadConfig,
    listRules,
    createRule,
    draftRuleWithAi,
    runRuleAiGoldenProbe,
    listRuleAiAudit,
    getRuleAiQualitySummary,
    updateRule,
    deleteRule,
    reorderRules,
    previewRules,
    explainRule,
    listProcessingEvents,
    getAiSettings,
    updateAiSettings,
    testAi,
    listAiTriggers,
    createAiTrigger,
    updateAiTrigger,
    deleteAiTrigger,
    previewAiTriggers,
  } = useApi();
  const [showAddModal, setShowAddModal] = useState(false);
  const [formData, setFormData] = useState({ display_name: '', email: '', app_password: '' });
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [testError, setTestError] = useState<string | null>(null);
  const [softDeletedId, setSoftDeletedId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [rules, setRules] = useState<MailRule[]>([]);
  const [selectedRuleId, setSelectedRuleId] = useState<number | 'new' | null>(null);
  const [ruleDraft, setRuleDraft] = useState<MailRuleInput>(newRuleDraft(10, null));
  const [ruleStatus, setRuleStatus] = useState<string | null>(null);
  const [ruleError, setRuleError] = useState<string | null>(null);
  const [aiRuleRequest, setAiRuleRequest] = useState('');
  const [aiRuleMode, setAiRuleMode] = useState<'auto' | 'sender_suppression' | 'alert_rule'>('auto');
  const [aiRuleDraft, setAiRuleDraft] = useState<RuleAiDraftResult | null>(null);
  const [aiRuleBusy, setAiRuleBusy] = useState(false);
  const [goldenProbeBusy, setGoldenProbeBusy] = useState(false);
  const [goldenProbeResult, setGoldenProbeResult] = useState<RuleAiGoldenProbeResponse | null>(null);
  const [goldenProbeError, setGoldenProbeError] = useState<string | null>(null);
  const [ruleAiQuality, setRuleAiQuality] = useState<RuleAiQualitySummary | null>(null);
  const [ruleAiAuditRecent, setRuleAiAuditRecent] = useState<RuleAiAuditItem[]>([]);
  const [ruleAiQualityError, setRuleAiQualityError] = useState<string | null>(null);
  const [previewText, setPreviewText] = useState(JSON.stringify(samplePreview, null, 2));
  const [previewResult, setPreviewResult] = useState<RulePreviewResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [explainText, setExplainText] = useState(JSON.stringify(samplePreview, null, 2));
  const [explainResult, setExplainResult] = useState<RuleExplainResponse | null>(null);
  const [explainError, setExplainError] = useState<string | null>(null);
  const [auditEvents, setAuditEvents] = useState<MailProcessingEvent[]>([]);
  const [aiSettings, setAiSettings] = useState<AiSettings | null>(null);
  const [aiStatus, setAiStatus] = useState<string | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiTestResult, setAiTestResult] = useState<AiClassification | null>(null);
  const [aiTriggers, setAiTriggers] = useState<AiTrigger[]>([]);
  const [aiTriggerDraft, setAiTriggerDraft] = useState<AiTriggerInput>(newAiTriggerDraft());
  const [selectedAiTriggerId, setSelectedAiTriggerId] = useState<string | 'new' | null>(null);
  const [aiTriggerStatus, setAiTriggerStatus] = useState<string | null>(null);
  const [aiTriggerError, setAiTriggerError] = useState<string | null>(null);
  const [aiTriggerPreview, setAiTriggerPreview] = useState<AiTriggerPreviewResult | null>(null);
  const activeAccounts = activeRuleAccounts(accounts);

  useEffect(() => {
    refreshRulesAndAudit();
    refreshRuleAiQuality();
    refreshAiSettings();
    refreshAiTriggers();
  }, []);

  useEffect(() => {
    if ((selectedRuleId === null || selectedRuleId === 'new') && ruleDraft.account_id === null) {
      const accountId = defaultRuleAccountId(accounts);
      if (accountId !== null) {
        setRuleDraft((draft) => ({ ...draft, account_id: accountId }));
      }
    }
  }, [accounts, selectedRuleId, ruleDraft.account_id]);

  const refreshRulesAndAudit = async () => {
    setRuleError(null);
    try {
      const [nextRules, events] = await Promise.all([
        listRules(),
        listProcessingEvents(50),
      ]);
      setRules(nextRules);
      setAuditEvents(events);
      if (selectedRuleId && selectedRuleId !== 'new') {
        const selected = nextRules.find((r) => r.rule_id === selectedRuleId);
        if (selected) setRuleDraft(ruleToDraft(selected));
      }
    } catch (e: any) {
      setRuleError(e.message);
    }
  };

  const refreshRuleAiQuality = async () => {
    setRuleAiQualityError(null);
    try {
      const [summary, recent] = await Promise.all([
        getRuleAiQualitySummary(),
        listRuleAiAudit({ limit: 5 }),
      ]);
      setRuleAiQuality(summary);
      setRuleAiAuditRecent(recent);
    } catch (e: any) {
      setRuleAiQualityError(e.message);
    }
  };

  const refreshAiSettings = async () => {
    setAiError(null);
    try {
      setAiSettings(await getAiSettings());
    } catch (e: any) {
      setAiError(e.message);
    }
  };

  const saveAiSettings = async () => {
    if (!aiSettings) return;
    setAiStatus('Saving AI settings...');
    setAiError(null);
    try {
      const saved = await updateAiSettings(aiSettings);
      setAiSettings(saved);
      setAiStatus('AI settings saved.');
      await reloadConfig();
    } catch (e: any) {
      setAiStatus(null);
      setAiError(e.message);
    }
  };

  const runAiTest = async () => {
    setAiStatus('Testing Ollama...');
    setAiError(null);
    setAiTestResult(null);
    try {
      const result = await testAi({
        sender: 'alerts@example.com',
        subject: 'Payment due reminder',
        body: 'Your payment is due tomorrow. Please review your account.',
      });
      setAiTestResult(result);
      setAiStatus('AI test completed.');
    } catch (e: any) {
      setAiStatus(null);
      setAiError(e.message);
    }
  };

  const refreshAiTriggers = async () => {
    setAiTriggerError(null);
    try {
      const triggers = await listAiTriggers();
      setAiTriggers(triggers);
      if (selectedAiTriggerId && selectedAiTriggerId !== 'new') {
        const selected = triggers.find((t) => t.trigger_id === selectedAiTriggerId);
        if (selected) setAiTriggerDraft(triggerToDraft(selected));
      }
    } catch (e: any) {
      setAiTriggerError(e.message);
    }
  };

  const saveAiTrigger = async () => {
    setAiTriggerStatus('Saving AI trigger...');
    setAiTriggerError(null);
    try {
      if (selectedAiTriggerId === 'new' || selectedAiTriggerId === null) {
        const created = await createAiTrigger(aiTriggerDraft);
        setSelectedAiTriggerId(created.trigger_id);
      } else {
        await updateAiTrigger(selectedAiTriggerId, aiTriggerDraft);
      }
      setAiTriggerStatus('AI trigger saved.');
      await refreshAiTriggers();
    } catch (e: any) {
      setAiTriggerStatus(null);
      setAiTriggerError(e.message);
    }
  };

  const runAiTriggerPreview = async () => {
    setAiTriggerPreview(null);
    setAiTriggerError(null);
    try {
      const savedPreview = await previewAiTriggers({
        category: 'payment_due',
        urgency_score: 8,
        confidence: 0.9,
        summary: 'Payment is due tomorrow.',
        needs_reply: true,
        reason: 'Payment reminder requires review.',
      });
      setAiTriggerPreview(savedPreview);
    } catch (e: any) {
      setAiTriggerError(e.message);
    }
  };

  const removeAiTrigger = async (triggerId: string) => {
    if (!confirm('Delete this AI trigger?')) return;
    setAiTriggerError(null);
    try {
      await deleteAiTrigger(triggerId);
      if (selectedAiTriggerId === triggerId) {
        setSelectedAiTriggerId(null);
        setAiTriggerDraft(newAiTriggerDraft());
      }
      await refreshAiTriggers();
    } catch (e: any) {
      setAiTriggerError(e.message);
    }
  };

  const selectAiTrigger = (trigger: AiTrigger) => {
    setSelectedAiTriggerId(trigger.trigger_id);
    setAiTriggerDraft(triggerToDraft(trigger));
    setAiTriggerStatus(null);
    setAiTriggerError(null);
    setAiTriggerPreview(null);
  };

  const startNewAiTrigger = () => {
    setSelectedAiTriggerId('new');
    setAiTriggerDraft(newAiTriggerDraft(aiTriggers.length + 1));
    setAiTriggerStatus(null);
    setAiTriggerError(null);
    setAiTriggerPreview(null);
  };

  const triggerToDraft = (trigger: AiTrigger): AiTriggerInput => ({
    name: trigger.name,
    enabled: trigger.enabled,
    priority: trigger.priority,
    conditions_json: trigger.conditions_json,
    actions_json: trigger.actions_json,
    cooldown_seconds: trigger.cooldown_seconds,
  });

  const ruleToDraft = (rule: MailRule): MailRuleInput => ({
    account_id: rule.account_id,
    name: rule.name,
    priority: rule.priority,
    enabled: rule.enabled,
    match_type: rule.match_type,
    conditions: rule.conditions.length ? rule.conditions.map(stripConditionIds) : [],
    actions: rule.actions.length ? rule.actions.map(stripActionIds) : [],
  });

  const stripConditionIds = (condition: MailRuleCondition): MailRuleCondition => ({
    field: condition.field,
    operator: condition.operator,
    value: condition.value ?? '',
    value_json: condition.value_json ?? null,
    case_sensitive: Boolean(condition.case_sensitive),
  });

  const stripActionIds = (action: MailRuleAction): MailRuleAction => ({
    action_type: action.action_type,
    target: action.target ?? '',
    value_json: action.value_json ?? null,
    stop_processing: Boolean(action.stop_processing),
  });

  const selectRule = (rule: MailRule) => {
    setSelectedRuleId(rule.rule_id);
    setRuleDraft(ruleToDraft(rule));
    setRuleStatus(null);
    setRuleError(null);
  };

  const startNewRule = () => {
    const accountId = defaultRuleAccountId(accounts, ruleDraft.account_id);
    setSelectedRuleId('new');
    setRuleDraft(newRuleDraft(nextPriorityForScope(rules, accountId), accountId));
    setRuleStatus(null);
    setRuleError(null);
  };

  const saveRule = async () => {
    setRuleStatus('Saving rule...');
    setRuleError(null);
    const ignoreRuleId = typeof selectedRuleId === 'number' ? selectedRuleId : undefined;
    if (hasPriorityConflict(rules, ruleDraft.account_id, ruleDraft.priority, ignoreRuleId)) {
      setRuleStatus(null);
      setRuleError('Priority already exists within the selected account scope.');
      return;
    }
    try {
      if (selectedRuleId === 'new' || selectedRuleId === null) {
        const created = await createRule(ruleDraft);
        setSelectedRuleId(created.rule_id);
      } else {
        await updateRule(selectedRuleId, ruleDraft);
      }
      setRuleStatus('Rule saved.');
      await refreshRulesAndAudit();
    } catch (e: any) {
      setRuleStatus(null);
      setRuleError(e.message);
    }
  };

  const draftAiRule = async () => {
    setAiRuleBusy(true);
    setAiRuleDraft(null);
    setRuleStatus(null);
    setRuleError(null);
    try {
      const draft = await draftRuleWithAi({
        request_text: aiRuleRequest,
        account_id: ruleDraft.account_id,
        mode: aiRuleMode,
      });
      setAiRuleDraft(draft);
      await refreshRuleAiQuality();
    } catch (e: any) {
      setRuleError(e.message);
    } finally {
      setAiRuleBusy(false);
    }
  };

  const saveAiDraftRule = async () => {
    if (!aiRuleDraft?.rule) return;
    setAiRuleBusy(true);
    setRuleStatus('Saving rule...');
    setRuleError(null);
    try {
      const priority = nextPriorityForScope(rules, aiRuleDraft.rule.account_id);
      const payload = aiDraftToRuleInput(aiRuleDraft, priority);
      if (!payload) {
        throw new Error('Only safe local suppression or alert drafts can be saved.');
      }
      const created = await createRule(payload);
      setSelectedRuleId(created.rule_id);
      setRuleDraft(ruleToDraft(created));
      setRuleStatus('Rule saved.');
      setAiRuleRequest('');
      setAiRuleDraft(null);
      await refreshRulesAndAudit();
      await refreshRuleAiQuality();
    } catch (e: any) {
      setRuleStatus(null);
      setRuleError(e.message);
    } finally {
      setAiRuleBusy(false);
    }
  };

  const runGoldenProbe = async () => {
    setGoldenProbeBusy(true);
    setGoldenProbeError(null);
    try {
      const result = await runRuleAiGoldenProbe({
        prompt_ids: null,
        fail_fast: false,
        timeout_seconds: 120,
      });
      setGoldenProbeResult(result);
      await refreshRuleAiQuality();
    } catch (e: any) {
      setGoldenProbeError(e.message);
    } finally {
      setGoldenProbeBusy(false);
    }
  };

  const removeRule = async (ruleId: number) => {
    if (!confirm('Delete this rule?')) return;
    setRuleError(null);
    try {
      await deleteRule(ruleId);
      if (selectedRuleId === ruleId) {
        setSelectedRuleId(null);
        const accountId = defaultRuleAccountId(accounts);
        setRuleDraft(newRuleDraft(nextPriorityForScope(rules, accountId), accountId));
      }
      await refreshRulesAndAudit();
    } catch (e: any) {
      setRuleError(e.message);
    }
  };

  const moveRule = async (ruleId: number, direction: -1 | 1) => {
    const payload = reorderPayloadForScope(rules, ruleId, direction);
    if (payload.length === 0) return;
    try {
      await reorderRules(payload);
      await refreshRulesAndAudit();
    } catch (e: any) {
      setRuleError(e.message);
    }
  };

  const runPreview = async () => {
    setPreviewError(null);
    setPreviewResult(null);
    try {
      const message = JSON.parse(previewText);
      const result = await previewRules(message);
      setPreviewResult(result);
    } catch (e: any) {
      setPreviewError(e.message);
    }
  };

  const runRuleExplanation = async () => {
    setExplainError(null);
    setExplainResult(null);
    try {
      const message = JSON.parse(explainText);
      const result = await explainRule({
        message,
        rule_id: typeof selectedRuleId === 'number' ? selectedRuleId : null,
        include_disabled: true,
      });
      setExplainResult(result);
    } catch (e: any) {
      setExplainError(e.message);
    }
  };

  const loadAiDraftExplanationSample = () => {
    setExplainText(JSON.stringify(syntheticMessageFromAiDraft(aiRuleDraft), null, 2));
    setExplainResult(null);
    setExplainError(null);
  };

  const updateCondition = (index: number, patch: Partial<MailRuleCondition>) => {
    const next = [...ruleDraft.conditions];
    next[index] = { ...next[index], ...patch };
    setRuleDraft({ ...ruleDraft, conditions: next });
  };

  const updateAction = (index: number, patch: Partial<MailRuleAction>) => {
    const next = [...ruleDraft.actions];
    const updated = { ...next[index], ...patch };
    if (patch.action_type && !actionRequiresTarget(patch.action_type)) {
      updated.target = '';
    }
    next[index] = updated;
    setRuleDraft({ ...ruleDraft, actions: next });
  };

  const handleTest = async () => {
    setTestStatus('testing');
    setTestError(null);
    try {
      await testAccount({ email: formData.email, app_password: formData.app_password, display_name: formData.display_name });
      setTestStatus('success');
    } catch (e: any) {
      setTestStatus('error');
      setTestError(e.message);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    setTestError(null);
    try {
      const result = await addAccount(formData);
      if (result.ok) {
        setShowAddModal(false);
        setFormData({ display_name: '', email: '', app_password: '' });
        setTestStatus('idle');
        setSoftDeletedId(null);
        await reloadConfig();
      }
    } catch (e: any) {
      if (e instanceof ApiError && e.errorCode === 'soft_deleted_exists' && e.accountId) {
        setSoftDeletedId(e.accountId);
        setTestError(`${e.message} Reactivate the existing account instead of creating a new one.`);
      } else {
        alert(e.message);
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleReactivate = async (id: string) => {
    setIsSaving(true);
    try {
      await reactivateAccount(id);
      setShowAddModal(false);
      setFormData({ display_name: '', email: '', app_password: '' });
      setTestStatus('idle');
      setSoftDeletedId(null);
      await reloadConfig();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (accountId: string) => {
    if (confirm('Are you sure you want to remove this account from polling?')) {
      try {
        await deleteAccount(accountId, true);
        await reloadConfig();
      } catch (e: any) {
        alert(e.message);
      }
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">IMAP Accounts</h2>
        <button
          onClick={() => setShowAddModal(true)}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 transition-colors"
        >
          Add Gmail Account
        </button>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-900/50 p-4 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      <AiSettingsCard
        settings={aiSettings}
        setSettings={setAiSettings}
        status={aiStatus}
        error={aiError}
        testResult={aiTestResult}
        onRefresh={refreshAiSettings}
        onSave={saveAiSettings}
        onTest={runAiTest}
      />

      <AiTriggersCard
        triggers={aiTriggers}
        selectedTriggerId={selectedAiTriggerId}
        draft={aiTriggerDraft}
        setDraft={setAiTriggerDraft}
        status={aiTriggerStatus}
        error={aiTriggerError}
        preview={aiTriggerPreview}
        onRefresh={refreshAiTriggers}
        onNew={startNewAiTrigger}
        onSelect={selectAiTrigger}
        onSave={saveAiTrigger}
        onDelete={removeAiTrigger}
        onPreview={runAiTriggerPreview}
      />

      <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="bg-gray-800/50 text-gray-400 uppercase text-[10px] tracking-wider">
            <tr>
              <th className="px-6 py-3 font-semibold">Account</th>
              <th className="px-6 py-3 font-semibold">Status</th>
              <th className="px-6 py-3 font-semibold">Last Success</th>
              <th className="px-6 py-3 font-semibold text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {accounts.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-6 py-10 text-center text-gray-500">
                  No IMAP accounts configured
                </td>
              </tr>
            ) : (
              accounts.map((account) => (
                <tr key={account.id} className="hover:bg-gray-800/30 transition-colors">
                  <td className="px-6 py-4">
                    <div className="font-medium text-gray-200">{account.name}</div>
                    <div className="text-xs text-gray-500">{account.email}</div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-col gap-1">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium w-fit ${
                        account.status === 'active' ? 'bg-green-900/30 text-green-400' : 
                        account.status === 'inactive' ? 'bg-gray-800 text-gray-400' : 'bg-red-900/30 text-red-400'
                      }`}>
                        {account.status}
                      </span>
                      {!account.enabled && (
                        <span className="text-[10px] text-gray-500 italic">Disabled</span>
                      )}
                    </div>
                  </td>
                  <td className="px-6 py-4 text-gray-400">
                    {account.last_success_at ? new Date(account.last_success_at).toLocaleString() : 'Never'}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <button 
                      onClick={() => handleDelete(account.id)}
                      className="text-gray-500 hover:text-red-400 transition-colors"
                      title="Remove from polling"
                    >
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="pt-4 border-t border-gray-900">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white">Rules</h2>
            <p className="text-xs text-gray-500 mt-1">
              Deterministic rules remain the primary classifier. AI enrichment is read-only and does not mutate mailboxes.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={refreshRulesAndAudit}
              className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700 transition-colors"
            >
              Refresh
            </button>
            <button
              onClick={startNewRule}
              className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 transition-colors"
            >
              New Rule
            </button>
          </div>
        </div>

        {ruleError && (
          <div className="bg-red-900/20 border border-red-900/50 p-4 rounded-lg text-red-400 text-sm mb-4">
            {ruleError}
          </div>
        )}
        {ruleStatus && (
          <div className="bg-green-900/20 border border-green-900/50 p-4 rounded-lg text-green-400 text-sm mb-4">
            {ruleStatus}
          </div>
        )}

        <AiRuleBuilderCard
          requestText={aiRuleRequest}
          setRequestText={(value) => {
            setAiRuleRequest(value);
            setAiRuleDraft(null);
          }}
          mode={aiRuleMode}
          setMode={(value) => {
            setAiRuleMode(value);
            setAiRuleDraft(null);
          }}
          draft={aiRuleDraft}
          busy={aiRuleBusy}
          onDraft={draftAiRule}
          onSave={saveAiDraftRule}
        />

        <RuleAiGoldenProbeCard
          result={goldenProbeResult}
          busy={goldenProbeBusy}
          error={goldenProbeError}
          onRun={runGoldenProbe}
        />

        <RuleAiQualityCard
          summary={ruleAiQuality}
          recent={ruleAiAuditRecent}
          error={ruleAiQualityError}
        />

        <div className="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-4">
          <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
            <div className="px-4 py-3 bg-gray-800/50 text-[10px] uppercase tracking-wider text-gray-400 font-semibold">
              Rule Order
            </div>
            <div className="divide-y divide-gray-800">
              {rules.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-gray-500">
                  No rules configured
                </div>
              ) : (
                [...rules]
                  .sort((a, b) => a.priority - b.priority || a.rule_id - b.rule_id)
                  .map((rule, index, ordered) => (
                    <div
                      key={rule.rule_id}
                      className={`p-4 transition-colors ${
                        selectedRuleId === rule.rule_id ? 'bg-indigo-950/30' : 'hover:bg-gray-800/30'
                      }`}
                    >
                      <button
                        onClick={() => selectRule(rule)}
                        className="w-full text-left"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="font-medium text-gray-200">{rule.name}</div>
                            <div className="text-xs text-gray-500 mt-1">
                              {accountScopeLabel(rule.account_id, accounts)} · Priority {rule.priority} · {rule.match_type} · {rule.conditions.length} conditions · {rule.actions.length} actions
                            </div>
                            {ruleHasMutationAction(rule) && (
                              <div className="flex flex-wrap gap-1.5 mt-2">
                                <span className="px-2 py-0.5 rounded bg-amber-900/30 text-amber-300 text-[10px] font-medium">
                                  Mailbox action
                                </span>
                                <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-400 text-[10px] font-medium">
                                  Dry-run protected
                                </span>
                              </div>
                            )}
                          </div>
                          <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                            rule.enabled ? 'bg-green-900/30 text-green-400' : 'bg-gray-800 text-gray-500'
                          }`}>
                            {rule.enabled ? 'Enabled' : 'Disabled'}
                          </span>
                        </div>
                      </button>
                      <div className="flex items-center gap-2 mt-3">
                        <button
                          onClick={() => moveRule(rule.rule_id, -1)}
                          disabled={rulesInScope(rules, rule.account_id).findIndex((r) => r.rule_id === rule.rule_id) === 0}
                          className="px-2 py-1 text-xs rounded bg-gray-800 text-gray-300 hover:bg-gray-700 disabled:opacity-40"
                        >
                          Up
                        </button>
                        <button
                          onClick={() => moveRule(rule.rule_id, 1)}
                          disabled={rulesInScope(rules, rule.account_id).findIndex((r) => r.rule_id === rule.rule_id) === rulesInScope(rules, rule.account_id).length - 1}
                          className="px-2 py-1 text-xs rounded bg-gray-800 text-gray-300 hover:bg-gray-700 disabled:opacity-40"
                        >
                          Down
                        </button>
                        <button
                          onClick={() => removeRule(rule.rule_id)}
                          className="ml-auto px-2 py-1 text-xs rounded bg-red-950/40 text-red-300 hover:bg-red-900/50"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  ))
              )}
            </div>
          </div>

          <div className="space-y-4">
            <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-semibold text-white">
                  {selectedRuleId === 'new' ? 'Create Rule' : selectedRuleId ? 'Edit Rule' : 'Rule Editor'}
                </h3>
                <button
                  onClick={saveRule}
                  className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 transition-colors"
                >
                  {selectedRuleId === 'new' || selectedRuleId === null ? 'Create' : 'Save'}
                </button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
                <label className="md:col-span-2">
                  <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Name</span>
                  <input
                    value={ruleDraft.name}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, name: e.target.value })}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </label>
                <label className="md:col-span-2">
                  <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Account Scope</span>
                  <select
                    value={ruleDraft.account_id ?? '__global__'}
                    onChange={(e) => {
                      const accountId = e.target.value === '__global__' ? null : e.target.value;
                      const priority = accountId === ruleDraft.account_id
                        ? ruleDraft.priority
                        : nextPriorityForScope(rules, accountId);
                      setRuleDraft({
                        ...ruleDraft,
                        account_id: accountId,
                        priority,
                      });
                    }}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    {activeAccounts.map((account) => (
                      <option key={account.id} value={account.id}>
                        {accountOptionLabel(account)}
                      </option>
                    ))}
                    <option value="__global__">All accounts / global rule</option>
                  </select>
                </label>
                <label>
                  <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Priority</span>
                  <input
                    type="number"
                    value={ruleDraft.priority}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, priority: Number(e.target.value) })}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </label>
                <label>
                  <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Match</span>
                  <select
                    value={ruleDraft.match_type}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, match_type: e.target.value as 'ALL' | 'ANY' })}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="ALL">ALL</option>
                    <option value="ANY">ANY</option>
                  </select>
                </label>
              </div>
              <p className="text-xs text-gray-500 mt-2">
                Priority is unique within the selected account scope.
              </p>

              <label className="flex items-center gap-2 mt-4 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={ruleDraft.enabled}
                  onChange={(e) => setRuleDraft({ ...ruleDraft, enabled: e.target.checked })}
                  className="rounded border-gray-700 bg-gray-800"
                />
                Enabled
              </label>

              <RuleListEditor
                title="Conditions"
                emptyLabel="Add condition"
                items={ruleDraft.conditions}
                onAdd={() => setRuleDraft({ ...ruleDraft, conditions: [...ruleDraft.conditions, emptyCondition()] })}
                onRemove={(index) => setRuleDraft({ ...ruleDraft, conditions: ruleDraft.conditions.filter((_, i) => i !== index) })}
                render={(condition, index) => (
                  <div className="space-y-2">
                    <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_1.5fr_auto] gap-2">
                      <select
                        value={condition.field}
                        onChange={(e) => updateCondition(index, { field: e.target.value })}
                        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
                      >
                        {FIELDS.map((field) => <option key={field} value={field}>{field}</option>)}
                      </select>
                      <select
                        value={condition.operator}
                        onChange={(e) => updateCondition(index, { operator: e.target.value })}
                        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
                      >
                        {OPERATORS.map((op) => <option key={op} value={op}>{op}</option>)}
                      </select>
                      <input
                        value={condition.value ?? ''}
                        onChange={(e) => updateCondition(index, { value: e.target.value })}
                        placeholder="Value"
                        disabled={condition.operator === 'exists'}
                        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white disabled:opacity-40"
                      />
                      <label className="flex items-center gap-2 text-xs text-gray-400 whitespace-nowrap">
                        <input
                          type="checkbox"
                          checked={Boolean(condition.case_sensitive)}
                          onChange={(e) => updateCondition(index, { case_sensitive: e.target.checked })}
                        />
                        Case
                      </label>
                    </div>
                    <JsonValueEditor
                      label="value_json"
                      value={condition.value_json}
                      onChange={(value_json) => updateCondition(index, { value_json })}
                      placeholder='Optional JSON, for example ["billing@example.com"] for operator=in'
                    />
                  </div>
                )}
              />

              <RuleListEditor
                title="Actions"
                emptyLabel="Add action"
                items={ruleDraft.actions}
                onAdd={() => setRuleDraft({ ...ruleDraft, actions: [...ruleDraft.actions, emptyAction()] })}
                onRemove={(index) => setRuleDraft({ ...ruleDraft, actions: ruleDraft.actions.filter((_, i) => i !== index) })}
                render={(action, index) => (
                  <div className="space-y-2">
                    <div className={`grid grid-cols-1 gap-2 ${
                      actionRequiresTarget(action.action_type)
                        ? 'md:grid-cols-[1.5fr_1fr_auto]'
                        : 'md:grid-cols-[1.5fr_auto]'
                    }`}>
                      <select
                        value={action.action_type}
                        onChange={(e) => updateAction(index, { action_type: e.target.value })}
                        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
                      >
                        {SAFE_ACTIONS.map((actionType) => (
                          <option key={actionType} value={actionType}>{actionLabel(actionType)}</option>
                        ))}
                      </select>
                      {actionRequiresTarget(action.action_type) && (
                        <input
                          value={action.target ?? ''}
                          onChange={(e) => updateAction(index, { target: e.target.value })}
                          placeholder="Target folder"
                          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
                        />
                      )}
                      <label className="flex items-center gap-2 text-xs text-gray-400 whitespace-nowrap">
                        <input
                          type="checkbox"
                          checked={Boolean(action.stop_processing)}
                          onChange={(e) => updateAction(index, { stop_processing: e.target.checked })}
                        />
                        Stop
                      </label>
                    </div>
                    {isMutationAction(action.action_type) && (
                      <div className="rounded-lg border border-amber-900/40 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
                        Mailbox mutations only execute when agent mode is live and mail.imap_mutations.enabled=true. Otherwise they are audited as blocked or dry-run.
                      </div>
                    )}
                    <JsonValueEditor
                      label="value_json"
                      value={action.value_json}
                      onChange={(value_json) => updateAction(index, { value_json })}
                      placeholder="Optional JSON metadata"
                    />
                  </div>
                )}
              />
            </div>

            <div className="grid grid-cols-1 2xl:grid-cols-2 gap-4">
              <PreviewPanel
                previewText={previewText}
                setPreviewText={setPreviewText}
                previewResult={previewResult}
                previewError={previewError}
                runPreview={runPreview}
              />
              <RuleExplainPanel
                selectedRuleId={typeof selectedRuleId === 'number' ? selectedRuleId : null}
                explainText={explainText}
                setExplainText={setExplainText}
                explainResult={explainResult}
                explainError={explainError}
                runExplanation={runRuleExplanation}
                loadAiDraftSample={loadAiDraftExplanationSample}
                hasAiDraft={Boolean(aiRuleDraft?.rule)}
              />
              <AuditPanel events={auditEvents} refresh={refreshRulesAndAudit} />
            </div>
          </div>
        </div>
      </div>

      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl w-full max-w-md shadow-2xl">
            <div className="p-6 space-y-4">
              <h3 className="text-xl font-bold text-white">Add Gmail Account</h3>
              <p className="text-sm text-gray-400">
                Use a <span className="text-indigo-400">Gmail App Password</span>, not your normal password. 2-Step Verification must be enabled.
              </p>
              
              <div className="space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Display Name</label>
                  <input
                    type="text"
                    value={formData.display_name}
                    onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
                    placeholder="Personal Gmail"
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Gmail Address</label>
                  <input
                    type="email"
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                    placeholder="user@gmail.com"
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">App Password</label>
                  <input
                    type="password"
                    value={formData.app_password}
                    onChange={(e) => setFormData({ ...formData, app_password: normalizeAppPassword(e.target.value) })}
                    placeholder="xxxx xxxx xxxx xxxx"
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </div>

              {testStatus === 'error' && (
                <div className="text-red-400 text-xs bg-red-900/20 p-3 rounded-lg border border-red-900/50">
                  {testError}
                </div>
              )}
              {testStatus === 'success' && (
                <div className="text-green-400 text-xs bg-green-900/20 p-3 rounded-lg border border-green-900/50">
                  Connection successful!
                </div>
              )}
              {softDeletedId && (
                <div className="text-amber-300 text-xs bg-amber-900/20 p-3 rounded-lg border border-amber-900/50">
                  This Gmail address already exists as a soft-deleted account. Reactivate it instead of creating a duplicate.
                </div>
              )}

              <div className="flex gap-3 pt-4">
                <button
                  onClick={() => setShowAddModal(false)}
                  className="flex-1 px-4 py-2 bg-gray-800 text-gray-300 rounded-lg font-medium hover:bg-gray-700 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleTest}
                  disabled={testStatus === 'testing' || !formData.email || !formData.app_password}
                  className="flex-1 px-4 py-2 bg-gray-700 text-white rounded-lg font-medium hover:bg-gray-600 disabled:opacity-50 transition-colors"
                >
                  {testStatus === 'testing' ? 'Testing...' : 'Test Connection'}
                </button>
                <button
                  onClick={handleSave}
                  disabled={testStatus !== 'success' || isSaving}
                  className="flex-1 px-4 py-2 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-500 disabled:opacity-50 transition-colors"
                >
                  {isSaving ? 'Saving...' : 'Save'}
                </button>
                {softDeletedId && (
                  <button
                    onClick={() => handleReactivate(softDeletedId)}
                    disabled={isSaving}
                    className="flex-1 px-4 py-2 bg-amber-600 text-white rounded-lg font-medium hover:bg-amber-500 disabled:opacity-50 transition-colors"
                  >
                    Reactivate
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AiRuleBuilderCard({
  requestText,
  setRequestText,
  mode,
  setMode,
  draft,
  busy,
  onDraft,
  onSave,
}: {
  requestText: string;
  setRequestText: (value: string) => void;
  mode: 'auto' | 'sender_suppression' | 'alert_rule';
  setMode: (value: 'auto' | 'sender_suppression' | 'alert_rule') => void;
  draft: RuleAiDraftResult | null;
  busy: boolean;
  onDraft: () => void;
  onSave: () => void;
}) {
  const saveable = isSaveableAiDraft(draft);
  const isAlertDraft = draft?.safety_status === 'safe_local_alert_draft';
  const failedAlertDraft = draft && !draft.rule && (draft.safety_status === 'llm_draft_failed' || draft.safety_status === 'local_llm_disabled');

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-white">AI Rule Builder</h3>
          <div className="mt-1 flex flex-wrap gap-2">
            <span className="px-2 py-0.5 rounded bg-emerald-950/50 text-emerald-300 text-[10px] font-medium">
              AI Rule Draft
            </span>
            <span className="px-2 py-0.5 rounded bg-sky-950/50 text-sky-300 text-[10px] font-medium">
              Safe Local Suppression
            </span>
            <span className="px-2 py-0.5 rounded bg-teal-950/50 text-teal-300 text-[10px] font-medium">
              Safe Local Alert Draft
            </span>
            <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-[10px] font-medium">
              This does not mutate Gmail
            </span>
            <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-[10px] font-medium">
              This does not send an iMessage now
            </span>
            <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-[10px] font-medium">
              Requires Save Rule
            </span>
          </div>
        </div>
        <button
          onClick={onDraft}
          disabled={busy || !requestText.trim()}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 disabled:opacity-50 transition-colors"
        >
          {busy ? 'Drafting...' : 'Draft Rule'}
        </button>
      </div>

      <div className="mt-4 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(340px,460px)] gap-4">
        <textarea
          value={requestText}
          onChange={(e) => setRequestText(e.target.value)}
          placeholder="If the mail is from Permata Bank asking for clarification on credit card transaction, send me an iMessage notification"
          rows={4}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
        <div className="lg:col-start-1 -mt-2">
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as 'auto' | 'sender_suppression' | 'alert_rule')}
            className="w-full max-w-sm bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
          >
            <option value="auto">Auto</option>
            <option value="sender_suppression">Sender suppression</option>
            <option value="alert_rule">Alert rule probe</option>
          </select>
        </div>

        <div className="rounded-lg border border-gray-800 bg-gray-950/50 p-4 min-h-32">
          {!draft ? (
            <div className="text-sm text-gray-500">Drafts are local Mail Agent proposals and require Save Rule.</div>
          ) : (
            <div className="space-y-3">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-500">AI Rule Draft</div>
                <div className="text-sm font-semibold text-gray-100 mt-1">{draft.intent_summary}</div>
                <div className="text-xs text-gray-500 mt-1">
                  {draft.safety_status === 'safe_local_suppression' ? 'Safe Local Suppression' : isAlertDraft ? 'Safe Local Alert Draft' : humanizeDraftStatus(draft.safety_status)}
                  {' '}· confidence {Math.round(draft.confidence * 100)}%
                </div>
                {(draft.provider || draft.model) && (
                  <div className="text-xs text-gray-500 mt-1">
                    {[draft.provider, draft.model].filter(Boolean).join(' / ')}
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-sky-900/50 bg-sky-950/20 p-3 text-xs text-sky-100">
                This does not mutate Gmail. This does not send an iMessage now. Save Rule uses the existing human-triggered rule creation API.
              </div>

              {draft.rule ? (
                <div className="space-y-2 text-xs">
                  <DraftInfo label="Rule name" value={draft.rule.name} />
                  <DraftInfo label="Account scope" value={draft.rule.account_id ?? 'Global'} />
                  <DraftInfo
                    label="Conditions"
                    value={draft.rule.conditions.map((condition) => `${condition.field} ${condition.operator} ${condition.value}`).join(', ')}
                  />
                  <DraftInfo
                    label="Actions"
                    value={draft.rule.actions.map((action) => action.action_type).join(', ')}
                  />
                </div>
              ) : (
                <div className="rounded-lg border border-amber-900/50 bg-amber-950/20 p-3 text-xs text-amber-100">
                  {failedAlertDraft ? 'Local model could not create a safe draft. No rule was saved.' : 'No saveable rule was drafted for this request.'}
                </div>
              )}

              <DraftList title="Explanation" items={draft.explanation} />
              <DraftList title="Warnings" items={draft.warnings} warning />

              {saveable && (
                <button
                  onClick={onSave}
                  disabled={busy}
                  className="w-full px-4 py-2 bg-emerald-700 text-white rounded-lg text-sm font-medium hover:bg-emerald-600 disabled:opacity-50 transition-colors"
                >
                  {busy ? 'Saving...' : 'Save Rule'}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RuleAiGoldenProbeCard({
  result,
  busy,
  error,
  onRun,
}: {
  result: RuleAiGoldenProbeResponse | null;
  busy: boolean;
  error: string | null;
  onRun: () => void;
}) {
  const disabled = result?.status === 'disabled';
  const failed = result?.status === 'failed';
  const passed = result?.status === 'passed';

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-white">Rule AI Golden Probe</h3>
          <p className="mt-1 text-sm text-gray-400">Manual local-model quality check. Drafts only. Saves nothing.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <ProbeChip>Does not save rules</ProbeChip>
            <ProbeChip>Does not send iMessage</ProbeChip>
            <ProbeChip>Does not mutate Gmail</ProbeChip>
            <ProbeChip>Does not call IMAP</ProbeChip>
          </div>
        </div>
        <button
          onClick={onRun}
          disabled={busy}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 disabled:opacity-50 transition-colors"
        >
          {busy ? 'Running...' : 'Run Golden Probe'}
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-900/50 bg-red-950/20 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {result && (
        <div className="mt-4 space-y-4">
          <div className={`rounded-lg border p-3 text-sm ${
            disabled
              ? 'border-amber-900/50 bg-amber-950/20 text-amber-100'
              : failed
                ? 'border-red-900/50 bg-red-950/20 text-red-100'
                : 'border-emerald-900/50 bg-emerald-950/20 text-emerald-100'
          }`}>
            <div className="font-semibold">
              {disabled ? 'Rule AI golden probe disabled' : passed ? 'Rule AI golden probe passed' : 'Rule AI golden probe failed'}
            </div>
            <div className="mt-1 text-xs opacity-90">
              {result.summary.passed} passed / {result.summary.failed} failed / {result.summary.total} total
              {result.summary.skipped ? ` / ${result.summary.skipped} skipped` : ''}
              {result.rule_ai ? ` · ${result.rule_ai.provider} / ${result.rule_ai.model}` : ''}
            </div>
            {disabled && (
              <div className="mt-2 text-xs">
                [mail.rule_ai].enabled is false. Enable only when intentionally testing local Ollama rule drafting.
              </div>
            )}
            {result.warnings?.map((warning, index) => (
              <div key={`${warning}-${index}`} className="mt-2 text-xs opacity-90">{warning}</div>
            ))}
          </div>

          {result.results.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-gray-800">
              <table className="min-w-full divide-y divide-gray-800 text-xs">
                <thead className="bg-gray-950/60 text-gray-500">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">Prompt ID</th>
                    <th className="px-3 py-2 text-left font-medium">Expected Domain</th>
                    <th className="px-3 py-2 text-left font-medium">Actual Domain</th>
                    <th className="px-3 py-2 text-left font-medium">Status</th>
                    <th className="px-3 py-2 text-left font-medium">First Error</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-800 bg-gray-950/30 text-gray-300">
                  {result.results.map((item) => (
                    <tr key={item.id}>
                      <td className="px-3 py-2 font-medium text-gray-100">{item.id}</td>
                      <td className="px-3 py-2">{item.expected_domain}</td>
                      <td className="px-3 py-2">{item.actual_domain || 'n/a'}</td>
                      <td className="px-3 py-2">
                        <span className={`rounded px-2 py-0.5 text-[10px] font-medium ${
                          item.passed ? 'bg-emerald-950/50 text-emerald-300' : 'bg-red-950/50 text-red-300'
                        }`}>
                          {item.passed ? 'passed' : 'failed'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-gray-400">{item.errors?.[0] || 'none'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ProbeChip({ children }: { children: ReactNode }) {
  return (
    <span className="px-2 py-0.5 rounded bg-gray-800 text-gray-300 text-[10px] font-medium">
      {children}
    </span>
  );
}

function humanizeDraftStatus(value: string) {
  return value.replace(/_/g, ' ');
}

function RuleAiQualityCard({
  summary,
  recent,
  error,
}: {
  summary: RuleAiQualitySummary | null;
  recent: RuleAiAuditItem[];
  error: string | null;
}) {
  const latest = summary?.latest_golden_probe;
  const saveableRate = summary ? Math.round(summary.saveable_rate * 100) : 0;

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-white">Rule AI Quality</h3>
          <p className="mt-1 text-sm text-gray-400">
            Audit stores request hashes and short previews only. It does not store raw model output or save rules.
          </p>
        </div>
        {latest && (
          <span className={`rounded px-2 py-1 text-xs font-medium ${
            latest.status === 'passed'
              ? 'bg-emerald-950/50 text-emerald-300'
              : latest.status === 'failed'
                ? 'bg-red-950/50 text-red-300'
                : 'bg-amber-950/50 text-amber-300'
          }`}>
            Latest probe: {latest.status}
          </span>
        )}
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-900/50 bg-red-950/20 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">
        <QualityMetric label="Draft attempts" value={summary?.total_draft_attempts ?? 0} />
        <QualityMetric label="Saveable drafts" value={summary?.saveable_count ?? 0} />
        <QualityMetric label="Unsupported/failed" value={(summary?.unsupported_count ?? 0) + (summary?.failed_count ?? 0)} />
        <QualityMetric label="Saveable rate" value={`${saveableRate}%`} />
      </div>

      {latest && (
        <div className="mt-4 rounded-lg border border-gray-800 bg-gray-950/40 p-3 text-xs text-gray-300">
          Latest golden probe: {latest.passed} passed / {latest.failed} failed / {latest.total} total
          {latest.skipped ? ` / ${latest.skipped} skipped` : ''}
          {latest.model ? ` · ${latest.model}` : ''}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-lg border border-gray-800">
        <div className="bg-gray-950/60 px-3 py-2 text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
          Recent AI draft attempts
        </div>
        {recent.length === 0 ? (
          <div className="px-3 py-5 text-sm text-gray-500">No draft audit rows yet.</div>
        ) : (
          <div className="divide-y divide-gray-800">
            {recent.map((item) => (
              <div key={item.id} className="px-3 py-3 text-xs">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-gray-100">{item.mode}</span>
                  <span className="text-gray-500">{item.status}</span>
                  <span className="text-gray-500">{item.safety_status}</span>
                  {item.model && <span className="text-gray-500">{item.model}</span>}
                  <span className="ml-auto text-gray-600">{new Date(item.created_at).toLocaleString()}</span>
                </div>
                <div className="mt-1 text-gray-300">{item.request_preview || 'No preview'}</div>
                <div className="mt-1 text-gray-500">
                  {item.rule_name || item.raw_model_error || item.normalized_intent || 'No rule drafted'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function QualityMetric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

function DraftInfo({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-gray-200 mt-1 break-words">{value}</div>
    </div>
  );
}

function DraftList({ title, items, warning }: { title: string; items: string[]; warning?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{title}</div>
      <div className="mt-1 space-y-1">
        {items.map((item, index) => (
          <div
            key={`${title}-${index}`}
            className={`rounded border px-2 py-1.5 text-xs break-words ${
              warning
                ? 'border-amber-900/50 bg-amber-950/20 text-amber-100'
                : 'border-gray-800 bg-gray-950/40 text-gray-300'
            }`}
          >
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

function AiSettingsCard({
  settings,
  setSettings,
  status,
  error,
  testResult,
  onRefresh,
  onSave,
  onTest,
}: {
  settings: AiSettings | null;
  setSettings: (settings: AiSettings) => void;
  status: string | null;
  error: string | null;
  testResult: AiClassification | null;
  onRefresh: () => void;
  onSave: () => void;
  onTest: () => void;
}) {
  const patch = (updates: Partial<AiSettings>) => {
    if (!settings) return;
    setSettings({ ...settings, ...updates });
  };

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-white">AI Enrichment</h2>
          <p className="text-xs text-gray-500 mt-1">
            Read-only Ollama classification queue. Mailbox actions remain disabled for this phase.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onRefresh}
            className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700 transition-colors"
          >
            Refresh
          </button>
          <button
            onClick={onTest}
            disabled={!settings}
            className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700 disabled:opacity-50 transition-colors"
          >
            Test
          </button>
          <button
            onClick={onSave}
            disabled={!settings}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 disabled:opacity-50 transition-colors"
          >
            Save
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-900/50 p-3 rounded-lg text-red-400 text-sm mb-4">
          {error}
        </div>
      )}
      {status && (
        <div className="bg-green-900/20 border border-green-900/50 p-3 rounded-lg text-green-400 text-sm mb-4">
          {status}
        </div>
      )}

      {!settings ? (
        <div className="text-sm text-gray-500">AI settings unavailable</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              type="checkbox"
              checked={settings.enabled}
              onChange={(e) => patch({ enabled: e.target.checked })}
            />
            Enabled
          </label>
          <label>
            <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Provider</span>
            <input
              value={settings.provider}
              readOnly
              className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-gray-400"
            />
          </label>
          <label className="md:col-span-2">
            <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Base URL</span>
            <input
              value={settings.base_url}
              onChange={(e) => patch({ base_url: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
            />
          </label>
          <label>
            <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Model</span>
            <input
              value={settings.model}
              onChange={(e) => patch({ model: e.target.value })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
            />
          </label>
          <label>
            <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Timeout</span>
            <input
              type="number"
              value={settings.timeout_seconds}
              onChange={(e) => patch({ timeout_seconds: Number(e.target.value) })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
            />
          </label>
          <label>
            <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Max Body Chars</span>
            <input
              type="number"
              value={settings.max_body_chars}
              onChange={(e) => patch({ max_body_chars: Number(e.target.value) })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
            />
          </label>
          <label>
            <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Urgency Threshold</span>
            <input
              type="number"
              min={0}
              max={10}
              value={settings.urgency_threshold}
              onChange={(e) => patch({ urgency_threshold: Number(e.target.value) })}
              className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
            />
          </label>
        </div>
      )}

      {testResult && (
        <div className="mt-4 bg-gray-950/70 border border-gray-800 rounded-lg p-3 text-sm">
          <div className="text-gray-300">
            {testResult.category} · urgency {testResult.urgency_score}/10 · confidence {Math.round(testResult.confidence * 100)}%
          </div>
          <div className="text-gray-500 mt-1">{testResult.summary}</div>
        </div>
      )}
    </div>
  );
}

const AI_TRIGGER_FIELDS = ['category', 'urgency_score', 'confidence', 'needs_reply', 'summary', 'reason'];
const AI_TRIGGER_ACTIONS = [
  'notify_dashboard',
  'send_imessage',
  'move_to_folder',
  'mark_read',
  'mark_flagged',
  'add_to_needs_reply',
];

function AiTriggersCard({
  triggers,
  selectedTriggerId,
  draft,
  setDraft,
  status,
  error,
  preview,
  onRefresh,
  onNew,
  onSelect,
  onSave,
  onDelete,
  onPreview,
}: {
  triggers: AiTrigger[];
  selectedTriggerId: string | 'new' | null;
  draft: AiTriggerInput;
  setDraft: (draft: AiTriggerInput) => void;
  status: string | null;
  error: string | null;
  preview: AiTriggerPreviewResult | null;
  onRefresh: () => void;
  onNew: () => void;
  onSelect: (trigger: AiTrigger) => void;
  onSave: () => void;
  onDelete: (triggerId: string) => void;
  onPreview: () => void;
}) {
  const updateCondition = (index: number, patch: Partial<AiTriggerCondition>) => {
    const next = [...draft.conditions_json.conditions];
    const updated = { ...next[index], ...patch };
    if (patch.field) {
      updated.operator = defaultAiTriggerOperator(patch.field);
      updated.value = defaultAiTriggerValue(patch.field);
    }
    setDraft({
      ...draft,
      conditions_json: { ...draft.conditions_json, conditions: next.map((c, i) => i === index ? updated : c) },
    });
  };
  const updateAction = (index: number, patch: Partial<any>) => {
    const next = [...draft.actions_json];
    const updated = { ...next[index], ...patch };
    if (patch.action_type && patch.action_type !== 'move_to_folder') {
      updated.target = null;
    }
    next[index] = updated;
    setDraft({ ...draft, actions_json: next });
  };

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold text-white">AI Triggers</h2>
          <p className="text-xs text-amber-200 mt-1">
            AI triggers are preview-only in this phase. They write audit events but do not move, mark, send, reply, forward, or delete emails.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={onRefresh} className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700">Refresh</button>
          <button onClick={onNew} className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700">New</button>
          <button onClick={onSave} className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500">
            {selectedTriggerId === 'new' || selectedTriggerId === null ? 'Create' : 'Save'}
          </button>
        </div>
      </div>

      {error && <div className="bg-red-900/20 border border-red-900/50 p-3 rounded-lg text-red-400 text-sm mb-4">{error}</div>}
      {status && <div className="bg-green-900/20 border border-green-900/50 p-3 rounded-lg text-green-400 text-sm mb-4">{status}</div>}

      <div className="grid grid-cols-1 xl:grid-cols-[340px_1fr] gap-4">
        <div className="border border-gray-800 rounded-lg overflow-hidden">
          {triggers.length === 0 ? (
            <div className="p-4 text-sm text-gray-500">No AI triggers configured.</div>
          ) : triggers.map((trigger) => (
            <button
              key={trigger.trigger_id}
              onClick={() => onSelect(trigger)}
              className={`w-full text-left p-3 border-b border-gray-800 last:border-b-0 ${selectedTriggerId === trigger.trigger_id ? 'bg-indigo-950/30' : 'hover:bg-gray-800/30'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-gray-200">{trigger.name}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded ${trigger.enabled ? 'bg-green-900/30 text-green-400' : 'bg-gray-800 text-gray-500'}`}>
                  {trigger.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>
              <div className="text-xs text-gray-500 mt-1">Priority {trigger.priority} · {trigger.actions_json.length} preview actions</div>
            </button>
          ))}
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
            <label className="md:col-span-2">
              <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Name</span>
              <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white" />
            </label>
            <label>
              <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Priority</span>
              <input type="number" value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })} className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white" />
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-300 mt-6">
              <input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />
              Enabled
            </label>
          </div>

          <RuleListEditor
            title="AI Conditions"
            emptyLabel="Add condition"
            items={draft.conditions_json.conditions}
            onAdd={() => setDraft({ ...draft, conditions_json: { ...draft.conditions_json, conditions: [...draft.conditions_json.conditions, emptyAiTriggerCondition()] } })}
            onRemove={(index) => setDraft({ ...draft, conditions_json: { ...draft.conditions_json, conditions: draft.conditions_json.conditions.filter((_, i) => i !== index) } })}
            render={(condition, index) => (
              <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_1.2fr] gap-2">
                <select value={condition.field} onChange={(e) => updateCondition(index, { field: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white">
                  {AI_TRIGGER_FIELDS.map((field) => <option key={field} value={field}>{field}</option>)}
                </select>
                <select value={condition.operator} onChange={(e) => updateCondition(index, { operator: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white">
                  {operatorsForAiTriggerField(condition.field).map((op) => <option key={op} value={op}>{op}</option>)}
                </select>
                <input value={String(condition.value ?? '')} onChange={(e) => updateCondition(index, { value: parseAiTriggerValue(condition.field, e.target.value) })} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white" />
              </div>
            )}
          />

          <RuleListEditor
            title="Preview Actions"
            emptyLabel="Add action"
            items={draft.actions_json}
            onAdd={() => setDraft({ ...draft, actions_json: [...draft.actions_json, { action_type: 'notify_dashboard' }] })}
            onRemove={(index) => setDraft({ ...draft, actions_json: draft.actions_json.filter((_, i) => i !== index) })}
            render={(action, index) => (
              <div className={`grid grid-cols-1 gap-2 ${action.action_type === 'move_to_folder' ? 'md:grid-cols-[1fr_1fr]' : 'md:grid-cols-[1fr]'}`}>
                <select value={action.action_type} onChange={(e) => updateAction(index, { action_type: e.target.value })} className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white">
                  {AI_TRIGGER_ACTIONS.map((actionType) => <option key={actionType} value={actionType}>{actionLabel(actionType)}</option>)}
                </select>
                {action.action_type === 'move_to_folder' && (
                  <input value={action.target ?? ''} onChange={(e) => updateAction(index, { target: e.target.value })} placeholder="Target folder" className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white" />
                )}
              </div>
            )}
          />

          <div className="flex items-center gap-2">
            <button onClick={onPreview} className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700">Preview</button>
            {selectedTriggerId && selectedTriggerId !== 'new' && (
              <button onClick={() => onDelete(selectedTriggerId)} className="px-3 py-2 bg-red-950/40 text-red-300 rounded-lg text-sm font-medium hover:bg-red-900/50">Delete</button>
            )}
          </div>

          {preview && (
            <div className="border border-gray-800 rounded-lg p-3 bg-gray-950/40 text-sm">
              <div className={preview.matched ? 'text-green-400' : 'text-gray-400'}>
                Preview matched: {String(preview.matched)}
              </div>
              <div className="mt-2 space-y-1">
                {preview.results.map((result) => (
                  <div key={result.trigger_id} className="text-xs text-gray-400">
                    {result.name}: {result.matched ? 'matched' : 'not matched'} · {result.reason}
                  </div>
                ))}
              </div>
              {preview.planned_actions.length > 0 && (
                <div className="mt-2 text-xs text-amber-200">
                  Planned dry-run actions: {preview.planned_actions.map((a) => actionLabel(a.action_type)).join(', ')}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function operatorsForAiTriggerField(field: string) {
  if (field === 'urgency_score' || field === 'confidence') return ['>=', '<=', '='];
  if (field === 'category') return ['equals', 'in'];
  if (field === 'needs_reply') return ['equals'];
  return ['contains', 'equals', 'in'];
}

function defaultAiTriggerOperator(field: string) {
  return operatorsForAiTriggerField(field)[0];
}

function defaultAiTriggerValue(field: string) {
  if (field === 'urgency_score') return 7;
  if (field === 'confidence') return 0.8;
  if (field === 'needs_reply') return true;
  if (field === 'category') return 'payment_due';
  return '';
}

function parseAiTriggerValue(field: string, raw: string) {
  if (field === 'urgency_score') return Number(raw);
  if (field === 'confidence') return Number(raw);
  if (field === 'needs_reply') return raw === 'true' || raw === '1';
  if (field === 'category' && raw.includes(',')) return raw.split(',').map((v) => v.trim()).filter(Boolean);
  return raw;
}

function RuleListEditor<T>({
  title,
  emptyLabel,
  items,
  onAdd,
  onRemove,
  render,
}: {
  title: string;
  emptyLabel: string;
  items: T[];
  onAdd: () => void;
  onRemove: (index: number) => void;
  render: (item: T, index: number) => ReactNode;
}) {
  return (
    <div className="mt-6">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-gray-200">{title}</h4>
        <button
          onClick={onAdd}
          className="px-3 py-1.5 bg-gray-800 text-gray-300 rounded-lg text-xs font-medium hover:bg-gray-700 transition-colors"
        >
          {emptyLabel}
        </button>
      </div>
      <div className="space-y-2">
        {items.length === 0 ? (
          <div className="border border-dashed border-gray-800 rounded-lg p-4 text-sm text-gray-500">
            No {title.toLowerCase()} configured
          </div>
        ) : (
          items.map((item, index) => (
            <div key={index} className="bg-gray-950/50 border border-gray-800 rounded-lg p-3">
              <div className="flex items-start gap-3">
                <div className="flex-1">{render(item, index)}</div>
                <button
                  onClick={() => onRemove(index)}
                  className="px-2 py-1 text-xs rounded bg-red-950/40 text-red-300 hover:bg-red-900/50"
                >
                  Remove
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function JsonValueEditor({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: any;
  onChange: (value: any) => void;
  placeholder: string;
}) {
  const [text, setText] = useState(value == null ? '' : JSON.stringify(value, null, 2));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(value == null ? '' : JSON.stringify(value, null, 2));
    setError(null);
  }, [value]);

  const handleChange = (next: string) => {
    setText(next);
    if (!next.trim()) {
      setError(null);
      onChange(null);
      return;
    }
    try {
      onChange(JSON.parse(next));
      setError(null);
    } catch {
      setError('Invalid JSON; fix before saving.');
    }
  };

  return (
    <label className="block">
      <span className="block text-[10px] font-medium text-gray-500 uppercase tracking-wider mb-1">{label}</span>
      <textarea
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        rows={2}
        placeholder={placeholder}
        className={`w-full bg-gray-950 border rounded-lg px-3 py-2 text-xs font-mono text-gray-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
          error ? 'border-red-900/70' : 'border-gray-800'
        }`}
      />
      {error && <span className="text-[10px] text-red-400">{error}</span>}
    </label>
  );
}

function PreviewPanel({
  previewText,
  setPreviewText,
  previewResult,
  previewError,
  runPreview,
}: {
  previewText: string;
  setPreviewText: (value: string) => void;
  previewResult: RulePreviewResult | null;
  previewError: string | null;
  runPreview: () => void;
}) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-base font-semibold text-white">Preview</h3>
          <p className="text-xs text-gray-500 mt-1">Side-effect-free evaluation against a sample message payload.</p>
        </div>
        <button
          onClick={runPreview}
          className="px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 transition-colors"
        >
          Run Preview
        </button>
      </div>

      <textarea
        value={previewText}
        onChange={(e) => setPreviewText(e.target.value)}
        rows={10}
        className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm font-mono text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
      />

      {previewError && (
        <div className="mt-3 bg-red-900/20 border border-red-900/50 p-3 rounded-lg text-red-400 text-sm">
          {previewError}
        </div>
      )}

      {previewResult && (
        <div className="mt-4 space-y-4 text-sm">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <PreviewFlag label="would_skip_ai" value={previewResult.would_skip_ai} />
            <PreviewFlag label="continue_to_classifier" value={previewResult.continue_to_classifier} />
            <PreviewFlag label="route_to_pdf_pipeline" value={previewResult.route_to_pdf_pipeline} />
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Matched Conditions</h4>
            <div className="space-y-2">
              {previewResult.matched_conditions.length === 0 ? (
                <div className="text-gray-500">No conditions matched.</div>
              ) : (
                previewResult.matched_conditions.map((rule) => (
                  <div key={rule.rule_id} className="border border-gray-800 rounded-lg p-3 bg-gray-950/40">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className="font-medium text-gray-200">{rule.name}</span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                        rule.matched ? 'bg-green-900/30 text-green-400' : 'bg-gray-800 text-gray-500'
                      }`}>
                        {rule.matched ? 'matched' : 'not matched'}
                      </span>
                    </div>
                    <div className="space-y-1">
                      {rule.conditions.map((condition, index) => (
                        <div key={index} className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                          <span className={condition.matched ? 'text-green-400' : 'text-gray-500'}>
                            {condition.matched ? 'match' : 'miss'}
                          </span>
                          <code className="text-gray-300">{condition.field}</code>
                          <span>{condition.operator}</span>
                          <code className="text-gray-300">{String(condition.value ?? '')}</code>
                        </div>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Planned Actions</h4>
            <div className="space-y-2">
              {previewResult.planned_actions.length === 0 ? (
                <div className="text-gray-500">No actions planned.</div>
              ) : (
                previewResult.planned_actions.map((action, index) => (
                  <div key={`${action.rule_id}-${action.action_type}-${index}`} className="flex items-start justify-between gap-2 border border-gray-800 rounded-lg p-3 bg-gray-950/40">
                    <div>
                      <div className="font-mono text-xs text-gray-200">{actionLabel(action.action_type)}</div>
                      {action.target && <div className="text-xs text-gray-500 mt-1">Target: {action.target}</div>}
                      {action.mutation && (
                        <div className="text-xs text-gray-500 mt-1">
                          Gate: <span className="text-amber-300">{action.gate_status}</span>
                          {typeof action.would_execute === 'boolean' && (
                            <span> · would execute: {String(action.would_execute)}</span>
                          )}
                          {action.reason && <span> · {action.reason}</span>}
                        </div>
                      )}
                    </div>
                    <span className="text-[10px] uppercase tracking-wider text-gray-500">rule {action.rule_id}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function RuleExplainPanel({
  selectedRuleId,
  explainText,
  setExplainText,
  explainResult,
  explainError,
  runExplanation,
  loadAiDraftSample,
  hasAiDraft,
}: {
  selectedRuleId: number | null;
  explainText: string;
  setExplainText: (value: string) => void;
  explainResult: RuleExplainResponse | null;
  explainError: string | null;
  runExplanation: () => void;
  loadAiDraftSample: () => void;
  hasAiDraft: boolean;
}) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <h3 className="text-base font-semibold text-white">Explain Rule</h3>
          <p className="text-xs text-gray-500 mt-1">Dry-run only. Does not send iMessage. Does not mutate Gmail. Does not call IMAP.</p>
        </div>
        <button
          onClick={runExplanation}
          className="px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 transition-colors"
        >
          Run Dry-Run Explanation
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3 text-xs text-gray-500">
        <span>Rule: {selectedRuleId ? `#${selectedRuleId}` : 'all enabled rules'}</span>
        {hasAiDraft && (
          <button
            onClick={loadAiDraftSample}
            className="px-2 py-1 rounded bg-gray-800 text-gray-300 hover:bg-gray-700"
          >
            Use AI draft sample
          </button>
        )}
      </div>

      <textarea
        value={explainText}
        onChange={(e) => setExplainText(e.target.value)}
        rows={8}
        className="w-full bg-gray-950 border border-gray-800 rounded-lg px-3 py-2 text-sm font-mono text-gray-200 focus:outline-none focus:ring-2 focus:ring-indigo-500"
      />

      {explainError && (
        <div className="mt-3 bg-red-900/20 border border-red-900/50 p-3 rounded-lg text-red-400 text-sm">
          {explainError}
        </div>
      )}

      {explainResult && (
        <div className="mt-4 space-y-4 text-sm">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
            <PreviewFlag label="matched" value={explainResult.matched_rule_count > 0} />
            <PreviewFlag label="would_skip_ai" value={explainResult.would_skip_ai} />
            <PreviewFlag label="stopped" value={explainResult.stopped} />
            <PreviewFlag label="route_pdf" value={explainResult.route_to_pdf_pipeline} />
          </div>

          <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-3 text-xs text-gray-400">
            <div>Sender domain: <span className="text-gray-200">{explainResult.message_summary.sender_domain || 'none'}</span></div>
            <div>Subject: <span className="text-gray-200">{explainResult.message_summary.subject || 'none'}</span></div>
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Conditions</h4>
            <div className="space-y-2">
              {explainResult.rules.length === 0 ? (
                <div className="text-gray-500">No saved rules were evaluated.</div>
              ) : (
                explainResult.rules.map((rule) => (
                  <div key={rule.rule_id} className="border border-gray-800 rounded-lg p-3 bg-gray-950/40">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <span className="font-medium text-gray-200">{rule.name}</span>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                        rule.matched ? 'bg-green-900/30 text-green-400' : 'bg-gray-800 text-gray-500'
                      }`}>
                        {rule.matched ? 'matched' : 'not matched'}
                      </span>
                    </div>
                    <div className="space-y-1">
                      {rule.conditions.map((condition, index) => (
                        <div key={index} className="grid grid-cols-1 md:grid-cols-[80px_1fr_1fr] gap-2 text-xs">
                          <span className={condition.matched ? 'text-green-400' : 'text-gray-500'}>
                            {condition.matched ? 'match' : 'miss'}
                          </span>
                          <div className="text-gray-400">
                            <code className="text-gray-300">{condition.field}</code> {condition.operator} <code className="text-gray-300">{String(condition.expected ?? '')}</code>
                          </div>
                          <div className="text-gray-500">
                            actual: <code className="text-gray-300">{String(condition.actual ?? '')}</code>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Planned Local Actions</h4>
            <div className="space-y-2">
              {explainResult.planned_actions.length === 0 ? (
                <div className="text-gray-500">No actions planned.</div>
              ) : (
                explainResult.planned_actions.map((action, index) => (
                  <div key={`${action.rule_id}-${action.action_type}-${index}`} className="border border-gray-800 rounded-lg p-3 bg-gray-950/40">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-gray-200">{actionLabel(action.action_type)}</span>
                      <span className="text-[10px] uppercase tracking-wider text-gray-500">preview only</span>
                    </div>
                    {action.target && <div className="text-xs text-gray-500 mt-1">Target: {action.target}</div>}
                    <div className="text-xs text-gray-500 mt-1">{action.explanation}</div>
                    {action.mutation && (
                      <div className="text-xs text-amber-300 mt-1">
                        Mutation dry-run gate: {action.gate_status || 'preview_only'} · would execute: {String(action.would_execute)}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs">
            <PreviewFlag label="Dry-run only" value={explainResult.safety.read_only} />
            <PreviewFlag label="No iMessage" value={!explainResult.safety.sent_imessage} />
            <PreviewFlag label="No Gmail mutation" value={!explainResult.safety.mutated_gmail} />
            <PreviewFlag label="No IMAP call" value={!explainResult.safety.called_imap} />
          </div>
        </div>
      )}
    </div>
  );
}

function PreviewFlag({ label, value }: { label: string; value: boolean }) {
  return (
    <div className="bg-gray-950/50 border border-gray-800 rounded-lg p-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={value ? 'text-green-400 font-medium mt-1' : 'text-gray-400 font-medium mt-1'}>
        {String(value)}
      </div>
    </div>
  );
}

function AuditPanel({
  events,
  refresh,
}: {
  events: MailProcessingEvent[];
  refresh: () => void;
}) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-base font-semibold text-white">Audit</h3>
          <p className="text-xs text-gray-500 mt-1">Recent mail_processing_events, newest first.</p>
        </div>
        <button
          onClick={refresh}
          className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700 transition-colors"
        >
          Refresh
        </button>
      </div>

      <div className="space-y-2 max-h-[560px] overflow-auto pr-1">
        {events.length === 0 ? (
          <div className="border border-dashed border-gray-800 rounded-lg p-6 text-sm text-gray-500 text-center">
            No processing events found
          </div>
        ) : (
          events.map((event) => (
            <div key={event.id} className="border border-gray-800 rounded-lg p-3 bg-gray-950/40">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium text-gray-200">{event.event_type}</div>
                  <div className="text-xs text-gray-500 mt-1">
                    {event.action_type || 'no action'} · {event.outcome}
                  </div>
                </div>
                <div className="text-right text-[10px] text-gray-500 whitespace-nowrap">
                  {new Date(event.created_at).toLocaleString()}
                </div>
              </div>
              <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-1 text-xs text-gray-500">
                <div>message: <span className="text-gray-300">{event.message_id || 'unknown'}</span></div>
                <div>account: <span className="text-gray-300">{event.account_id || 'global'}</span></div>
                <div>rule: <span className="text-gray-300">{event.rule_id ?? 'n/a'}</span></div>
                <div>bridge: <span className="text-gray-300">{event.bridge_id || 'n/a'}</span></div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
