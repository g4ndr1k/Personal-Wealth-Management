import { useState } from 'react';
import Dashboard from './views/Dashboard';
import { ApiProvider } from './api/mail';

type Tab = 'dashboard' | 'emails' | 'drafts' | 'settings';

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard');

  const tabs: { id: Tab; label: string }[] = [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'emails', label: 'Emails' },
    { id: 'drafts', label: 'Drafts' },
    { id: 'settings', label: 'Settings' },
  ];

  return (
    <ApiProvider>
      <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
        {/* Main content */}
        <div className="flex-1 overflow-auto p-6">
          {activeTab === 'dashboard' && <Dashboard />}
          {activeTab === 'emails' && (
            <PlaceholderTab title="Emails" />
          )}
          {activeTab === 'drafts' && (
            <PlaceholderTab title="Drafts" />
          )}
          {activeTab === 'settings' && (
            <PlaceholderTab title="Settings" />
          )}
        </div>

        {/* Tab bar */}
        <div className="border-t border-gray-800 px-6 py-3 flex gap-6 bg-gray-950">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`relative pb-1 text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? 'text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {tab.label}
              {activeTab === tab.id && (
                <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-white rounded-full" />
              )}
            </button>
          ))}
        </div>
      </div>
    </ApiProvider>
  );
}

function PlaceholderTab({ title }: { title: string }) {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="text-center">
        <h2 className="text-xl font-semibold text-gray-400">{title}</h2>
        <p className="text-gray-600 mt-2 text-sm">
          Coming in a future update
        </p>
      </div>
    </div>
  );
}
