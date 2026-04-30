import { useEffect, useState } from 'react';
import { MailActionApproval, useApi } from '../api/mail';

const STATUS_OPTIONS = ['pending', 'approved', 'executed', 'blocked', 'failed', 'rejected', 'expired'];
const UNSUPPORTED_ACTIONS = new Set([
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

function label(value: string | null | undefined) {
  return (value || 'none').replace(/_/g, ' ');
}

function statusClass(status: string) {
  if (status === 'pending') return 'bg-amber-900/30 text-amber-300';
  if (status === 'approved') return 'bg-indigo-900/30 text-indigo-300';
  if (status === 'executed') return 'bg-green-900/30 text-green-300';
  if (status === 'blocked') return 'bg-yellow-900/30 text-yellow-300';
  if (status === 'failed') return 'bg-red-900/30 text-red-300';
  return 'bg-gray-800 text-gray-400';
}

function actionSupport(actionType: string) {
  if (UNSUPPORTED_ACTIONS.has(actionType)) {
    return { label: 'Blocked in Phase 4D.1', className: 'bg-red-950/50 text-red-300 border-red-900/60' };
  }
  if (actionType === 'add_to_needs_reply') {
    return { label: 'Operator action', className: 'bg-green-950/40 text-green-300 border-green-900/50' };
  }
  return { label: 'Gate checked at execution', className: 'bg-yellow-950/40 text-yellow-300 border-yellow-900/50' };
}

export default function ControlCenter() {
  const {
    listApprovals,
    approveApproval,
    rejectApproval,
    executeApproval,
    expireApproval,
  } = useApi();
  const [status, setStatus] = useState('pending');
  const [approvals, setApprovals] = useState<MailActionApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, string>>({});

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setApprovals(await listApprovals(status, 50));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, [status]);

  const run = async (approvalId: string, action: string, fn: () => Promise<MailActionApproval>) => {
    setBusy((state) => ({ ...state, [approvalId]: action }));
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy((state) => {
        const next = { ...state };
        delete next[approvalId];
        return next;
      });
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-white">Control Center</h2>
          <p className="text-xs text-gray-500 mt-1 max-w-3xl">
            Operator review for AI-suggested actions. Approval only authorizes an execution attempt; agent mode, mutation settings, dry-run mode, UIDVALIDITY, and IMAP capabilities still decide the result.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="bg-gray-900 border border-gray-800 rounded-lg px-3 py-2 text-sm text-gray-200"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>{label(option)}</option>
            ))}
          </select>
          <button
            onClick={refresh}
            className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700"
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-900/50 p-4 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-gray-500 text-sm">Loading approvals...</div>
      ) : approvals.length === 0 ? (
        <div className="border border-dashed border-gray-800 rounded-lg p-10 text-center text-sm text-gray-500">
          No {label(status)} approvals
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {approvals.map((approval) => (
            <ApprovalCard
              key={approval.approval_id}
              approval={approval}
              busy={busy[approval.approval_id]}
              run={run}
              approveApproval={approveApproval}
              rejectApproval={rejectApproval}
              executeApproval={executeApproval}
              expireApproval={expireApproval}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ApprovalCard({
  approval,
  busy,
  run,
  approveApproval,
  rejectApproval,
  executeApproval,
  expireApproval,
}: {
  approval: MailActionApproval;
  busy?: string;
  run: (approvalId: string, action: string, fn: () => Promise<MailActionApproval>) => Promise<void>;
  approveApproval: (approvalId: string, decision_note?: string) => Promise<MailActionApproval>;
  rejectApproval: (approvalId: string, decision_note?: string) => Promise<MailActionApproval>;
  executeApproval: (approvalId: string) => Promise<MailActionApproval>;
  expireApproval: (approvalId: string) => Promise<MailActionApproval>;
}) {
  const support = actionSupport(approval.proposed_action_type);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 sm:p-5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${statusClass(approval.status)}`}>
                      {label(approval.status)}
                    </span>
                    <span className="text-xs text-gray-500">{label(approval.source_type)}</span>
                  </div>
                  <h3 className="text-sm font-semibold text-gray-100 mt-2 truncate">
                    {approval.subject || approval.message_key || approval.approval_id}
                  </h3>
                  <p className="text-xs text-gray-500 mt-1">
                    {approval.sender || 'unknown sender'} · {approval.requested_at ? new Date(approval.requested_at).toLocaleString() : 'unknown time'}
                  </p>
                </div>
                <div className="text-right text-xs text-gray-500">
                  {approval.account_id || 'no account'}
                  {approval.folder && <div>{approval.folder}</div>}
                </div>
              </div>

              <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
                <Metric label="AI category" value={label(approval.ai_category)} />
                <Metric label="Urgency" value={approval.ai_urgency_score == null ? 'n/a' : `${approval.ai_urgency_score}/10`} />
                <Metric label="Confidence" value={approval.ai_confidence == null ? 'n/a' : `${Math.round(approval.ai_confidence * 100)}%`} />
              </div>

              <div className="mt-4 rounded-lg border border-gray-800 bg-gray-950/50 p-3">
                <div className="text-[10px] uppercase tracking-wider text-gray-500">Proposed action</div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-gray-200">
                  <span>{label(approval.proposed_action_type)}</span>
                  {approval.proposed_target && <span className="text-gray-500">-&gt; {approval.proposed_target}</span>}
                  <span className={`rounded border px-2 py-0.5 text-[10px] font-medium ${support.className}`}>
                    {support.label}
                  </span>
                </div>
                {approval.reason && (
                  <p className="text-xs text-gray-500 mt-2">{approval.reason}</p>
                )}
              </div>

              {approval.execution_status && (
                <div className="mt-3 text-xs text-gray-400">
                  Execution: <span className="text-gray-200">{label(approval.execution_status)}</span>
                  {approval.execution_result?.reason && (
                    <span className="text-gray-500"> · {approval.execution_result.reason}</span>
                  )}
                  {approval.execution_result?.result?.status && (
                    <span className="text-gray-500"> · gate: {label(approval.execution_result.result.status)}</span>
                  )}
                </div>
              )}

              <div className="mt-4 flex flex-wrap items-center gap-2">
                {approval.status === 'pending' && (
                  <>
                    <button
                      onClick={() => run(approval.approval_id, 'approving', () => approveApproval(approval.approval_id, 'Approved from Control Center'))}
                      disabled={Boolean(busy)}
                      className="px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 disabled:opacity-50"
                    >
                      {busy === 'approving' ? 'Approving...' : 'Approve attempt'}
                    </button>
                    <button
                      onClick={() => run(approval.approval_id, 'rejecting', () => rejectApproval(approval.approval_id, 'Rejected from Control Center'))}
                      disabled={Boolean(busy)}
                      className="px-3 py-2 bg-red-950/50 text-red-300 rounded-lg text-sm font-medium hover:bg-red-900/60 disabled:opacity-50"
                    >
                      {busy === 'rejecting' ? 'Rejecting...' : 'Reject'}
                    </button>
                    <button
                      onClick={() => run(approval.approval_id, 'expiring', () => expireApproval(approval.approval_id))}
                      disabled={Boolean(busy)}
                      className="px-3 py-2 bg-gray-800 text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-700 disabled:opacity-50"
                    >
                      Expire
                    </button>
                  </>
                )}
                {approval.status === 'approved' && (
                  <button
                    onClick={() => run(approval.approval_id, 'executing', () => executeApproval(approval.approval_id))}
                    disabled={Boolean(busy)}
                    className="px-3 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-500 disabled:opacity-50"
                  >
                    {busy === 'executing' ? 'Executing...' : 'Run execution attempt'}
                  </button>
                )}
              </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/50 p-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className="text-gray-200 font-medium mt-1">{value}</div>
    </div>
  );
}
