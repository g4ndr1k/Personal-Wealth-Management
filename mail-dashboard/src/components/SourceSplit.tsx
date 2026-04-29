import { Doughnut } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  ArcElement,
  Tooltip,
  Legend,
  ChartOptions,
} from 'chart.js';

ChartJS.register(ArcElement, Tooltip, Legend);

interface Props {
  gmail: number;
  outlook: number;
}

export default function SourceSplit({ gmail, outlook }: Props) {
  const data = {
    labels: ['Gmail', 'Outlook'],
    datasets: [
      {
        data: [gmail, outlook],
        backgroundColor: ['#6366f1', '#f59e0b'],
        borderColor: ['#4f46e5', '#d97706'],
        borderWidth: 2,
        hoverOffset: 4,
      },
    ],
  };

  const options: ChartOptions<'doughnut'> = {
    responsive: true,
    maintainAspectRatio: false,
    cutout: '65%',
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          color: '#9ca3af',
          font: { size: 11 },
          padding: 12,
          usePointStyle: true,
          pointStyleWidth: 8,
        },
      },
    },
  };

  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
        Source Split
      </h3>
      <div className="h-40 flex items-center justify-center">
        <Doughnut data={data} options={options} />
      </div>
      <div className="mt-2 space-y-1">
        <div className="flex justify-between text-xs">
          <span className="text-gray-400">Gmail</span>
          <span className="text-gray-200">{gmail}</span>
        </div>
        <div className="flex justify-between text-xs">
          <span className="text-gray-400">Outlook</span>
          <span className="text-gray-200">{outlook}</span>
        </div>
      </div>
    </div>
  );
}
