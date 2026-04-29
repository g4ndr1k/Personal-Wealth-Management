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

interface RecentEmail {
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
}

interface AccountHealth {
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
        previewRules, listProcessingEvents
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
