import { ReactNode, useEffect, useState } from 'react';
import {
  ApiError,
  MailProcessingEvent,
  MailRule,
  MailRuleAction,
  MailRuleCondition,
  MailRuleInput,
  RulePreviewResult,
  useApi,
} from '../api/mail';

function normalizeAppPassword(value: string) {
  return value.replace(/\s+/g, '');
}

const SAFE_ACTIONS = [
  'mark_pending_alert',
  'skip_ai_inference',
  'add_to_needs_reply',
  'route_to_pdf_pipeline',
  'notify_dashboard',
  'stop_processing',
] as const;

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

const newRuleDraft = (priority: number): MailRuleInput => ({
  account_id: null,
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
    updateRule,
    deleteRule,
    reorderRules,
    previewRules,
    listProcessingEvents,
  } = useApi();
  const [showAddModal, setShowAddModal] = useState(false);
  const [formData, setFormData] = useState({ display_name: '', email: '', app_password: '' });
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'success' | 'error'>('idle');
  const [testError, setTestError] = useState<string | null>(null);
  const [softDeletedId, setSoftDeletedId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [rules, setRules] = useState<MailRule[]>([]);
  const [selectedRuleId, setSelectedRuleId] = useState<number | 'new' | null>(null);
  const [ruleDraft, setRuleDraft] = useState<MailRuleInput>(newRuleDraft(10));
  const [ruleStatus, setRuleStatus] = useState<string | null>(null);
  const [ruleError, setRuleError] = useState<string | null>(null);
  const [previewText, setPreviewText] = useState(JSON.stringify(samplePreview, null, 2));
  const [previewResult, setPreviewResult] = useState<RulePreviewResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [auditEvents, setAuditEvents] = useState<MailProcessingEvent[]>([]);

  useEffect(() => {
    refreshRulesAndAudit();
  }, []);

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
    const maxPriority = rules.reduce((max, rule) => Math.max(max, rule.priority), 0);
    setSelectedRuleId('new');
    setRuleDraft(newRuleDraft(maxPriority + 10));
    setRuleStatus(null);
    setRuleError(null);
  };

  const saveRule = async () => {
    setRuleStatus('Saving rule...');
    setRuleError(null);
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

  const removeRule = async (ruleId: number) => {
    if (!confirm('Delete this rule?')) return;
    setRuleError(null);
    try {
      await deleteRule(ruleId);
      if (selectedRuleId === ruleId) {
        setSelectedRuleId(null);
        setRuleDraft(newRuleDraft(10));
      }
      await refreshRulesAndAudit();
    } catch (e: any) {
      setRuleError(e.message);
    }
  };

  const moveRule = async (ruleId: number, direction: -1 | 1) => {
    const ordered = [...rules].sort((a, b) => a.priority - b.priority || a.rule_id - b.rule_id);
    const idx = ordered.findIndex((rule) => rule.rule_id === ruleId);
    const swapIdx = idx + direction;
    if (idx < 0 || swapIdx < 0 || swapIdx >= ordered.length) return;
    const swapped = [...ordered];
    [swapped[idx], swapped[swapIdx]] = [swapped[swapIdx], swapped[idx]];
    try {
      await reorderRules(swapped.map((rule, index) => ({
        rule_id: rule.rule_id,
        priority: (index + 1) * 10,
      })));
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

  const updateCondition = (index: number, patch: Partial<MailRuleCondition>) => {
    const next = [...ruleDraft.conditions];
    next[index] = { ...next[index], ...patch };
    setRuleDraft({ ...ruleDraft, conditions: next });
  };

  const updateAction = (index: number, patch: Partial<MailRuleAction>) => {
    const next = [...ruleDraft.actions];
    next[index] = { ...next[index], ...patch };
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
              Phase 4A deterministic rules only. Deferred IMAP mutations and AI settings are intentionally hidden.
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
                              Priority {rule.priority} · {rule.match_type} · {rule.conditions.length} conditions · {rule.actions.length} actions
                            </div>
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
                          disabled={index === 0}
                          className="px-2 py-1 text-xs rounded bg-gray-800 text-gray-300 hover:bg-gray-700 disabled:opacity-40"
                        >
                          Up
                        </button>
                        <button
                          onClick={() => moveRule(rule.rule_id, 1)}
                          disabled={index === ordered.length - 1}
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

              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <label className="md:col-span-2">
                  <span className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Name</span>
                  <input
                    value={ruleDraft.name}
                    onChange={(e) => setRuleDraft({ ...ruleDraft, name: e.target.value })}
                    className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
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
                    <div className="grid grid-cols-1 md:grid-cols-[1.5fr_1fr_auto] gap-2">
                      <select
                        value={action.action_type}
                        onChange={(e) => updateAction(index, { action_type: e.target.value })}
                        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
                      >
                        {SAFE_ACTIONS.map((actionType) => (
                          <option key={actionType} value={actionType}>{actionType}</option>
                        ))}
                      </select>
                      <input
                        value={action.target ?? ''}
                        onChange={(e) => updateAction(index, { target: e.target.value })}
                        placeholder="Target (optional)"
                        className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white"
                      />
                      <label className="flex items-center gap-2 text-xs text-gray-400 whitespace-nowrap">
                        <input
                          type="checkbox"
                          checked={Boolean(action.stop_processing)}
                          onChange={(e) => updateAction(index, { stop_processing: e.target.checked })}
                        />
                        Stop
                      </label>
                    </div>
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
                  <div key={`${action.rule_id}-${action.action_type}-${index}`} className="flex items-center justify-between gap-2 border border-gray-800 rounded-lg p-3 bg-gray-950/40">
                    <div>
                      <div className="font-mono text-xs text-gray-200">{action.action_type}</div>
                      {action.target && <div className="text-xs text-gray-500 mt-1">Target: {action.target}</div>}
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
