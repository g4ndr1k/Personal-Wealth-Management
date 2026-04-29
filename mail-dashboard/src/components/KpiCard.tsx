import { useApi } from '../api/mail';

interface Props {
  icon: string;
  label: string;
  value: string | number;
  subline?: string;
}

export default function KpiCard({ icon, label, value, subline }: Props) {
  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <div className="flex items-start gap-3">
        <span className="text-2xl">{icon}</span>
        <div>
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
            {label}
          </p>
          <p className="text-3xl font-bold mt-1">{value}</p>
          {subline && (
            <p className="text-xs text-gray-500 mt-1">{subline}</p>
          )}
        </div>
      </div>
    </div>
  );
}
