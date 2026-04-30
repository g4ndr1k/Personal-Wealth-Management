import { useState } from 'react';
import Dashboard from './views/Dashboard';
import Settings from './views/Settings';
import ControlCenter from './views/ControlCenter';
import { ApiProvider } from './api/mail';

type Tab = 'dashboard' | 'control' | 'emails' | 'drafts' | 'settings';

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard');

  const tabs: { id: Tab; label: string }[] = [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'control', label: 'Control Center' },
    { id: 'emails', label: 'Emails' },
    { id: 'drafts', label: 'Drafts' },
    { id: 'settings', label: 'Settings' },
  ];

  return (
    <ApiProvider>
      <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
        <header
          className="sticky top-0 z-50 bg-gray-950/95 backdrop-blur border-b border-gray-900/80"
          style={{ WebkitAppRegion: 'drag' } as any}
        >
          <div className="min-h-14 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 pl-20 pr-6 py-2">
            <span className="text-[10px] uppercase tracking-[0.3em] font-bold text-gray-500 whitespace-nowrap">
              Email Intelligence Hub
            </span>
            <nav
              className="flex min-w-0 items-center gap-6 overflow-x-auto"
              style={{ WebkitAppRegion: 'no-drag' } as any}
              aria-label="App navigation"
            >
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
            </nav>
          </div>
        </header>

        {/* Main content */}
        <main className="flex-1 p-6">
          {activeTab === 'dashboard' && <Dashboard />}
          {activeTab === 'control' && <ControlCenter />}
          {activeTab === 'emails' && (
            <PlaceholderTab title="Emails" />
          )}
          {activeTab === 'drafts' && (
            <PlaceholderTab title="Drafts" />
          )}
          {activeTab === 'settings' && <Settings />}
        </main>
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
