interface Props {
  classification: Record<string, number>;
}

const CATEGORY_ORDER = [
  'urgent',
  'important',
  'reply_needed',
  'personal',
  'newsletter',
  'automated',
  'spam',
];

const CATEGORY_LABELS: Record<string, string> = {
  urgent: 'Urgent',
  important: 'Important',
  reply_needed: 'Reply Needed',
  personal: 'Personal',
  newsletter: 'Newsletter',
  automated: 'Automated',
  spam: 'Spam',
  transaction_alert: 'Transaction Alert',
  bill_statement: 'Bill Statement',
  bank_clarification: 'Bank Clarification',
  payment_due: 'Payment Due',
  security_alert: 'Security Alert',
  financial_other: 'Financial Other',
  not_financial: 'Not Financial',
};

export default function ClassificationList({ classification }: Props) {
  const entries = CATEGORY_ORDER
    .filter((k) => (classification[k] || 0) > 0)
    .map((k) => ({ key: k, label: CATEGORY_LABELS[k] || k, count: classification[k] || 0 }));

  // Also add any categories not in the predefined order
  const extra = Object.entries(classification)
    .filter(([k, v]) => v > 0 && !CATEGORY_ORDER.includes(k))
    .map(([k, v]) => ({ key: k, label: CATEGORY_LABELS[k] || k, count: v }));

  const all = [...entries, ...extra];

  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        Classification
      </h3>
      <div className="space-y-2">
        {all.length === 0 && (
          <p className="text-gray-600 text-xs">No classifications yet</p>
        )}
        {all.map(({ key, label, count }) => (
          <div key={key} className="flex justify-between items-center text-sm">
            <span className="text-gray-300">{label}</span>
            <span className="text-gray-400 font-mono">{count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
