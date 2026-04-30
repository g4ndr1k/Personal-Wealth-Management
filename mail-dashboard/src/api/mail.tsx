import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';

const API_BASE = 'http://127.0.0.1:8090';

export class ApiError extends Error {
  status: number;
  errorCode?: string;
  accountId?: string;

  constructor(message: string, status = 0, errorCode?: string, accountId?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.errorCode = errorCode;
    this.accountId = accountId;
  }
}

interface Summary {
  total_processed: number;
  urgent_count: number;
  drafts_created: number;
  avg_priority: number;
  source_split: { gmail: number; outlook: number };
  classification: Record<string, number>;
  actions: {
    drafts_created: number;
    labels_applied: number;
    imessage_alerts: number;
    important_count: number;
    reply_needed_count: number;
  };
  mode: string;
}

export interface RecentEmail {
  bridge_id: string;
  message_id: string;
  processed_at: string;
  category: string;
  urgency: string;
  provider: string;
  alert_sent: number;
  summary: string;
  status: string;
  source: string;
  ai_queue_id?: number | null;
  ai_status?: string | null;
  ai_last_error?: string | null;
  ai_category?: string | null;
  ai_urgency_score?: number | null;
  ai_confidence?: number | null;
  ai_summary?: string | null;
}

export interface AiSettings {
  enabled: boolean;
  provider: string;
  base_url: string;
  model: string;
  temperature: number;
  timeout_seconds: number;
  max_body_chars: number;
  urgency_threshold: number;
}

export interface AiClassification {
  category: string;
  urgency_score: number;
  confidence: number;
  summary: string;
  needs_reply: boolean;
  reason: string;
}

export interface AiTriggerCondition {
  field: string;
  operator: string;
  value: any;
}

export interface AiTriggerAction {
  action_type: string;
  target?: string | null;
  value?: any;
  dry_run?: boolean;
  would_execute?: boolean;
  reason?: string;
}

export interface AiTrigger {
  trigger_id: string;
  name: string;
  enabled: boolean;
  priority: number;
  conditions_json: {
    match_type: 'ALL' | 'ANY';
    conditions: AiTriggerCondition[];
  };
  actions_json: AiTriggerAction[];
  cooldown_seconds: number;
  created_at: string;
  updated_at: string;
}

export type AiTriggerInput = Omit<AiTrigger, 'trigger_id' | 'created_at' | 'updated_at'>;

export interface AiTriggerPreviewResult {
  matched: boolean;
  results: Array<{
    trigger_id: string;
    name: string;
    priority: number;
    matched: boolean;
    matched_conditions: Array<AiTriggerCondition & { matched: boolean }>;
    planned_actions: AiTriggerAction[];
    dry_run: boolean;
    reason: string;
  }>;
  matched_conditions: Array<AiTriggerCondition & { matched: boolean }>;
  planned_actions: AiTriggerAction[];
}

export interface AccountHealth {
  id: string;
  name: string;
  email: string;
  provider: string;
  enabled: boolean;
  status: string;
  last_success_at: string | null;
  last_error: string | null;
}

export interface MailRuleCondition {
  id?: number;
  field: string;
  operator: string;
  value?: string | null;
  value_json?: any;
  case_sensitive?: boolean;
}

export interface MailRuleAction {
  id?: number;
  action_type: string;
  target?: string | null;
  value_json?: any;
  stop_processing?: boolean;
}

export interface MailRule {
  rule_id: number;
  account_id: string | null;
  name: string;
  priority: number;
  enabled: boolean;
  match_type: 'ALL' | 'ANY';
  created_at: string;
  updated_at: string;
  conditions: MailRuleCondition[];
  actions: MailRuleAction[];
}

export interface RulePreviewResult {
  matched_conditions: Array<{
    rule_id: number;
    name: string;
    matched: boolean;
    conditions: Array<MailRuleCondition & { matched: boolean }>;
  }>;
  planned_actions: Array<{
    rule_id: number;
    action_type: string;
    target?: string | null;
    value?: any;
    status?: string;
    would_execute?: boolean;
    gate_status?: string;
    reason?: string;
    dry_run?: boolean;
    mutation?: boolean;
  }>;
  would_skip_ai: boolean;
  continue_to_classifier: boolean;
  route_to_pdf_pipeline: boolean;
}

export interface MailProcessingEvent {
  id: number;
  message_id: string;
  account_id: string | null;
  bridge_id: string | null;
  rule_id: number | null;
  action_type: string | null;
  event_type: string;
  outcome: string;
  details_json: any;
  created_at: string;
}

export interface MailActionApproval {
  approval_id: string;
  source_type: 'ai_trigger' | 'manual' | 'rule_preview';
  source_id: string | null;
  message_key: string | null;
  account_id: string | null;
  folder: string | null;
  uidvalidity: string | null;
  imap_uid: number | null;
  subject: string | null;
  sender: string | null;
  received_at: string | null;
  proposed_action_type: string;
  proposed_target: string | null;
  action_type?: string;
  target?: string | null;
  proposed_value: any;
  proposed_value_json?: string | null;
  reason: string | null;
  ai_category: string | null;
  ai_urgency_score: number | null;
  ai_confidence: number | null;
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'executed' | 'failed' | 'blocked';
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  execution_started_at?: string | null;
  executed_at: string | null;
  archived_at?: string | null;
  is_archived?: boolean;
  execution_finished_at?: string | null;
  execution_status: string | null;
  execution_state?: 'not_requested' | 'started' | 'executed' | 'blocked' | 'failed' | 'expired' | 'rejected' | 'stuck';
  execution_error?: string | null;
  blocked_reason?: string | null;
  gate_result?: any;
  final_verification?: any;
  execution_mode?: string | null;
  preview_title?: string;
  preview_summary?: string;
  risk_level?: 'safe_readonly' | 'safe_reversible' | 'caution' | 'dangerous_blocked' | 'unsupported_blocked';
  risk_reasons?: string[];
  operator_guidance?: string;
  reversibility?: string;
  would_execute_now?: boolean;
  would_be_blocked_now?: boolean;
  current_gate_preview?: {
    would_execute_now: boolean;
    would_be_blocked_now: boolean;
    gate: string;
    reason: string;
    capability: string;
    notes: string[];
    mode?: string;
    mutation_enabled?: boolean;
    dry_run_default?: boolean;
    allow_mark_read?: boolean;
    allow_mark_unread?: boolean;
    allow_add_label?: boolean;
    allow_move_to_folder?: boolean;
    identity_complete?: boolean;
    uidvalidity_guard?: boolean;
    capability_cache?: any;
    folder_state?: any;
    dry_run_plan?: any;
    mutation_plan?: any;
    rollback_hint?: string | null;
    reversible?: boolean;
    safety_gates?: Array<{ gate: string; status: string; reason: string }>;
  };
  message_context?: {
    sender?: string | null;
    subject?: string | null;
    received_at?: string | null;
    account_id?: string | null;
    account_label?: string | null;
    folder?: string | null;
    imap_uid?: number | null;
    uidvalidity?: string | number | null;
    classification_category?: string | null;
    ai_summary?: string | null;
    urgency_score?: number | null;
    confidence?: number | null;
  };
  trigger_context?: any;
  rule_context?: any;
  expires_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  message_id?: string | null;
  trigger_id?: string | null;
  rule_id?: string | null;
  audit_event_ids?: number[];
  events?: MailApprovalEvent[];
  execution_result: any;
  created_at: string;
  updated_at: string;
}

export interface MailApprovalEvent {
  id: number;
  message_id: string | null;
  account_id: string | null;
  bridge_id: string | null;
  rule_id: number | null;
  action_type: string | null;
  event_type: string;
  outcome: string;
  details: any;
  created_at: string;
}

export interface ApprovalCleanupPreview {
  cleanup_enabled: boolean;
  would_expire_pending: number;
  would_archive_terminal: number;
  would_hard_delete: number;
  stuck_or_started_excluded: number;
  auto_expire_pending_after_hours: number;
  retain_audit_days: number;
  archive_terminal_after_days: number;
  examples: any;
  notes: string[];
}

export interface ApprovalListOptions {
  status?: string;
  execution_state?: string;
  include_archived?: boolean;
  risk_level?: string;
  limit?: number;
  offset?: number;
}

export type MailRuleInput = Omit<MailRule, 'rule_id' | 'created_at' | 'updated_at'>;

interface ApiContextType {
  summary: Summary | null;
  recent: RecentEmail[];
  accounts: AccountHealth[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  triggerRun: () => Promise<void>;
  testAccount: (data: any) => Promise<any>;
  addAccount: (data: any) => Promise<any>;
  deleteAccount: (accountId: string, purgeSecret?: boolean) => Promise<any>;
  reactivateAccount: (accountId: string) => Promise<any>;
  reloadConfig: () => Promise<any>;
  listRules: () => Promise<MailRule[]>;
  createRule: (data: MailRuleInput) => Promise<MailRule>;
  updateRule: (ruleId: number, data: Partial<MailRuleInput>) => Promise<MailRule>;
  deleteRule: (ruleId: number) => Promise<any>;
  reorderRules: (rules: Array<{ rule_id: number; priority: number }>) => Promise<any>;
  previewRules: (message: Record<string, any>) => Promise<RulePreviewResult>;
  listProcessingEvents: (limit?: number) => Promise<MailProcessingEvent[]>;
  getAiSettings: () => Promise<AiSettings>;
  updateAiSettings: (data: Partial<AiSettings>) => Promise<AiSettings>;
  testAi: (data: { sender: string; subject: string; body: string }) => Promise<AiClassification>;
  reprocessMessage: (messageId: string) => Promise<{ queue_id: number; status: string }>;
  listAiTriggers: () => Promise<AiTrigger[]>;
  createAiTrigger: (data: AiTriggerInput) => Promise<AiTrigger>;
  updateAiTrigger: (triggerId: string, data: Partial<AiTriggerInput>) => Promise<AiTrigger>;
  deleteAiTrigger: (triggerId: string) => Promise<any>;
  previewAiTriggers: (classification: Partial<AiClassification>) => Promise<AiTriggerPreviewResult>;
  listApprovals: (options?: ApprovalListOptions | string, limit?: number) => Promise<MailActionApproval[]>;
  getApproval: (approvalId: string) => Promise<MailActionApproval>;
  approveApproval: (approvalId: string, decision_note?: string) => Promise<MailActionApproval>;
  rejectApproval: (approvalId: string, decision_note?: string) => Promise<MailActionApproval>;
  executeApproval: (approvalId: string) => Promise<MailActionApproval>;
  expireApproval: (approvalId: string) => Promise<MailActionApproval>;
  markApprovalFailed: (approvalId: string, reason?: string) => Promise<MailActionApproval>;
  previewApprovalCleanup: () => Promise<ApprovalCleanupPreview>;
  cleanupApprovals: (force?: boolean) => Promise<any>;
  archiveApproval: (approvalId: string) => Promise<MailActionApproval>;
  unarchiveApproval: (approvalId: string) => Promise<MailActionApproval>;
  exportApprovals: (options?: ApprovalListOptions & { include_events?: boolean }) => Promise<any>;
}

const ApiContext = createContext<ApiContextType | null>(null);

async function parseApiResponse(resp: Response) {
  const contentType = resp.headers.get('content-type') || '';
  const text = await resp.text();
  const isJson = contentType.includes('application/json');
  const data = isJson && text ? JSON.parse(text) : null;

  if (!resp.ok) {
    const message =
      data?.detail ||
      (text.startsWith('<!DOCTYPE') || text.startsWith('<html')
        ? `Unexpected HTML response from ${resp.url}. The mail API route may not be mounted on the backend.`
        : text || `${resp.status}: ${resp.statusText}`);
    throw new ApiError(message, resp.status, data?.error_code, data?.account_id);
  }

  if (!isJson) {
    throw new ApiError(
      `Expected JSON from ${resp.url}, received ${contentType || 'unknown content type'}.`,
      resp.status,
    );
  }

  return data;
}

export function ApiProvider({ children }: { children: ReactNode }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [recent, setRecent] = useState<RecentEmail[]>([]);
  const [accounts, setAccounts] = useState<AccountHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const getApiKey = () => {
    // In Electron, read from env. In dev, from import.meta.env.
    return import.meta.env.VITE_FINANCE_API_KEY || '';
  };

  const fetchWithAuth = useCallback(async (path: string, init: RequestInit = {}) => {
    const key = getApiKey();
    const headers: Record<string, string> = {
      ...(init.headers as Record<string, string> | undefined),
    };
    if (key) headers['X-Api-Key'] = key;
    const resp = await fetch(`${API_BASE}${path}`, { ...init, headers });
    return parseApiResponse(resp);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sum, rec, acc] = await Promise.all([
        fetchWithAuth('/api/mail/summary'),
        fetchWithAuth('/api/mail/recent?limit=20'),
        fetchWithAuth('/api/mail/accounts'),
      ]);
      setSummary(sum);
      setRecent(rec.items || rec);
      setAccounts(acc.accounts || acc);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [fetchWithAuth]);

  const triggerRun = useCallback(async () => {
    const key = getApiKey();
    const headers: Record<string, string> = {};
    if (key) headers['X-Api-Key'] = key;
    await fetch(`${API_BASE}/api/mail/run`, {
      method: 'POST',
      headers,
    });
    // Wait a moment then refresh
    setTimeout(() => refresh(), 2000);
  }, [refresh]);

  const testAccount = useCallback(async (data: any) => {
    const key = getApiKey();
    const resp = await fetch(`${API_BASE}/api/mail/accounts/test`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Api-Key': key,
      },
      body: JSON.stringify(data),
    });
    return parseApiResponse(resp);
  }, []);

  const addAccount = useCallback(async (data: any) => {
    const key = getApiKey();
    const resp = await fetch(`${API_BASE}/api/mail/accounts`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Api-Key': key,
      },
      body: JSON.stringify(data),
    });
    const result = await parseApiResponse(resp);
    refresh();
    return result;
  }, [refresh]);

  const deleteAccount = useCallback(async (accountId: string, purgeSecret = false) => {
    const key = getApiKey();
    const resp = await fetch(`${API_BASE}/api/mail/accounts/${accountId}?purge_secret=${purgeSecret}`, {
      method: 'DELETE',
      headers: {
        'X-Api-Key': key,
      },
    });
    const result = await parseApiResponse(resp);
    refresh();
    return result;
  }, [refresh]);

  const reactivateAccount = useCallback(async (accountId: string) => {
    const key = getApiKey();
    const resp = await fetch(`${API_BASE}/api/mail/accounts/${accountId}/reactivate`, {
      method: 'POST',
      headers: {
        'X-Api-Key': key,
      },
    });
    const result = await parseApiResponse(resp);
    refresh();
    return result;
  }, [refresh]);

  const reloadConfig = useCallback(async () => {
    const key = getApiKey();
    const resp = await fetch(`${API_BASE}/api/mail/config/reload`, {
      method: 'POST',
      headers: {
        'X-Api-Key': key,
      },
    });
    return parseApiResponse(resp);
  }, []);

  const listRules = useCallback(async () => {
    return fetchWithAuth('/api/mail/rules');
  }, [fetchWithAuth]);

  const createRule = useCallback(async (data: MailRuleInput) => {
    return fetchWithAuth('/api/mail/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }, [fetchWithAuth]);

  const updateRule = useCallback(async (ruleId: number, data: Partial<MailRuleInput>) => {
    return fetchWithAuth(`/api/mail/rules/${ruleId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }, [fetchWithAuth]);

  const deleteRule = useCallback(async (ruleId: number) => {
    return fetchWithAuth(`/api/mail/rules/${ruleId}`, { method: 'DELETE' });
  }, [fetchWithAuth]);

  const reorderRules = useCallback(async (rules: Array<{ rule_id: number; priority: number }>) => {
    return fetchWithAuth('/api/mail/rules/reorder', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rules }),
    });
  }, [fetchWithAuth]);

  const previewRules = useCallback(async (message: Record<string, any>) => {
    return fetchWithAuth('/api/mail/rules/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
  }, [fetchWithAuth]);

  const listProcessingEvents = useCallback(async (limit = 50) => {
    return fetchWithAuth(`/api/mail/processing-events?limit=${limit}`);
  }, [fetchWithAuth]);

  const getAiSettings = useCallback(async () => {
    return fetchWithAuth('/api/mail/ai/settings');
  }, [fetchWithAuth]);

  const updateAiSettings = useCallback(async (data: Partial<AiSettings>) => {
    return fetchWithAuth('/api/mail/ai/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }, [fetchWithAuth]);

  const testAi = useCallback(async (data: { sender: string; subject: string; body: string }) => {
    return fetchWithAuth('/api/mail/ai/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }, [fetchWithAuth]);

  const reprocessMessage = useCallback(async (messageId: string) => {
    return fetchWithAuth(`/api/mail/messages/${encodeURIComponent(messageId)}/reprocess`, {
      method: 'POST',
    });
  }, [fetchWithAuth]);

  const listAiTriggers = useCallback(async () => {
    return fetchWithAuth('/api/mail/ai/triggers');
  }, [fetchWithAuth]);

  const createAiTrigger = useCallback(async (data: AiTriggerInput) => {
    return fetchWithAuth('/api/mail/ai/triggers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }, [fetchWithAuth]);

  const updateAiTrigger = useCallback(async (triggerId: string, data: Partial<AiTriggerInput>) => {
    return fetchWithAuth(`/api/mail/ai/triggers/${encodeURIComponent(triggerId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  }, [fetchWithAuth]);

  const deleteAiTrigger = useCallback(async (triggerId: string) => {
    return fetchWithAuth(`/api/mail/ai/triggers/${encodeURIComponent(triggerId)}`, {
      method: 'DELETE',
    });
  }, [fetchWithAuth]);

  const previewAiTriggers = useCallback(async (classification: Partial<AiClassification>) => {
    return fetchWithAuth('/api/mail/ai/triggers/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ classification }),
    });
  }, [fetchWithAuth]);

  const listApprovals = useCallback(async (options: ApprovalListOptions | string = { status: 'pending' }, limit = 50) => {
    const opts: ApprovalListOptions = typeof options === 'string' ? { status: options, limit } : options;
    const params = new URLSearchParams({ limit: String(opts.limit ?? 50) });
    if (opts.status) params.set('status', opts.status);
    if (opts.execution_state) params.set('execution_state', opts.execution_state);
    if (opts.include_archived) params.set('include_archived', 'true');
    if (opts.risk_level) params.set('risk_level', opts.risk_level);
    if (opts.offset) params.set('offset', String(opts.offset));
    return fetchWithAuth(`/api/mail/approvals?${params.toString()}`);
  }, [fetchWithAuth]);

  const getApproval = useCallback(async (approvalId: string) => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}`);
  }, [fetchWithAuth]);

  const approveApproval = useCallback(async (approvalId: string, decision_note = '') => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision_note }),
    });
  }, [fetchWithAuth]);

  const rejectApproval = useCallback(async (approvalId: string, decision_note = '') => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision_note }),
    });
  }, [fetchWithAuth]);

  const executeApproval = useCallback(async (approvalId: string) => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}/execute`, {
      method: 'POST',
    });
  }, [fetchWithAuth]);

  const expireApproval = useCallback(async (approvalId: string) => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}/expire`, {
      method: 'POST',
    });
  }, [fetchWithAuth]);

  const markApprovalFailed = useCallback(async (approvalId: string, reason = 'Execution started but did not finish') => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}/mark-failed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
  }, [fetchWithAuth]);

  const previewApprovalCleanup = useCallback(async () => {
    return fetchWithAuth('/api/mail/approvals/cleanup/preview');
  }, [fetchWithAuth]);

  const cleanupApprovals = useCallback(async (force = false) => {
    return fetchWithAuth('/api/mail/approvals/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force }),
    });
  }, [fetchWithAuth]);

  const archiveApproval = useCallback(async (approvalId: string) => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}/archive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decided_by: 'operator' }),
    });
  }, [fetchWithAuth]);

  const unarchiveApproval = useCallback(async (approvalId: string) => {
    return fetchWithAuth(`/api/mail/approvals/${encodeURIComponent(approvalId)}/unarchive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decided_by: 'operator' }),
    });
  }, [fetchWithAuth]);

  const exportApprovals = useCallback(async (options: ApprovalListOptions & { include_events?: boolean } = {}) => {
    const params = new URLSearchParams({
      format: 'json',
      include_events: String(options.include_events ?? true),
      limit: String(options.limit ?? 500),
    });
    if (options.status) params.set('status', options.status);
    if (options.execution_state) params.set('execution_state', options.execution_state);
    if (options.include_archived) params.set('include_archived', 'true');
    if (options.offset) params.set('offset', String(options.offset));
    return fetchWithAuth(`/api/mail/approvals/export?${params.toString()}`);
  }, [fetchWithAuth]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <ApiContext.Provider
      value={{ 
        summary, recent, accounts, loading, error, refresh, triggerRun,
        testAccount, addAccount, deleteAccount, reactivateAccount, reloadConfig,
        listRules, createRule, updateRule, deleteRule, reorderRules,
        previewRules, listProcessingEvents, getAiSettings, updateAiSettings,
        testAi, reprocessMessage, listAiTriggers, createAiTrigger,
        updateAiTrigger, deleteAiTrigger, previewAiTriggers,
        listApprovals, getApproval, approveApproval, rejectApproval,
        executeApproval, expireApproval, markApprovalFailed,
        previewApprovalCleanup, cleanupApprovals, archiveApproval,
        unarchiveApproval, exportApprovals
      }}
    >
      {children}
    </ApiContext.Provider>
  );
}

export function useApi() {
  const ctx = useContext(ApiContext);
  if (!ctx) throw new Error('useApi must be used within ApiProvider');
  return ctx;
}
