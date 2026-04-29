import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';

const API_BASE = 'http://127.0.0.1:8080';

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
  account_name: string;
  host: string;
  email: string;
  status: string;
  last_success_at: string | null;
  last_error: string | null;
}

interface ApiContextType {
  summary: Summary | null;
  recent: RecentEmail[];
  accounts: AccountHealth[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  triggerRun: () => Promise<void>;
}

const ApiContext = createContext<ApiContextType | null>(null);

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

  const fetchWithAuth = useCallback(async (path: string) => {
    const key = getApiKey();
    const headers: Record<string, string> = {};
    if (key) headers['X-Api-Key'] = key;
    const resp = await fetch(`${API_BASE}${path}`, { headers });
    if (!resp.ok) throw new Error(`${resp.status}: ${resp.statusText}`);
    return resp.json();
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

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <ApiContext.Provider
      value={{ summary, recent, accounts, loading, error, refresh, triggerRun }}
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
