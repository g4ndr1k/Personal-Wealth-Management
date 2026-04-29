interface Props {
  actions: {
    drafts_created: number;
    labels_applied: number;
    imessage_alerts: number;
    important_count: number;
    reply_needed_count: number;
  };
}

const ACTION_ITEMS = [
  { key: 'drafts_created', label: 'Drafts Created', color: 'bg-blue-500' },
  { key: 'labels_applied', label: 'Labels Applied', color: 'bg-green-500' },
  { key: 'imessage_alerts', label: 'iMessage Alerts', color: 'bg-yellow-500' },
  { key: 'important_count', label: 'Important', color: 'bg-red-500' },
  { key: 'reply_needed_count', label: 'Reply Needed', color: 'bg-purple-500' },
] as const;

export default function ActionsList({ actions }: Props) {
  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        Actions Taken
      </h3>
      <div className="space-y-2">
        {ACTION_ITEMS.map(({ key, label, color }) => (
          <div
            key={key}
            className="flex justify-between items-center text-sm"
          >
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${color}`} />
              <span className="text-gray-300">{label}</span>
            </div>
            <span className="text-gray-400 font-mono">
              {actions[key as keyof typeof actions] ?? 0}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
