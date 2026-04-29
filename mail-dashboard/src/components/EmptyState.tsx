export default function EmptyState() {
  return (
    <div className="bg-gray-900 rounded-xl p-8 border border-gray-800 flex flex-col items-center justify-center min-h-[200px]">
      <div className="text-5xl mb-4">✉️</div>
      <h3 className="text-lg font-semibold text-gray-300">
        No emails processed yet
      </h3>
      <p className="text-sm text-gray-500 mt-2 text-center">
        Click <span className="font-medium text-gray-400">Run Now</span> or
        wait for the 15-minute schedule.
      </p>
    </div>
  );
}
