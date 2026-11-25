import React, { useEffect, useRef, useState } from 'react';
import { X, Search } from 'lucide-react';
import { DEFAULT_CORE_LAYOUTS } from './LayoutControls';

export interface LayoutDef {
  id: string;
  name: string;
  category: string;
  description: string;
  implemented: boolean;
  aspect_ratio: string;
  default_params?: Record<string, any>;
  parameters_schema?: Record<string, any>;
}

interface LayoutModalProps {
  isOpen: boolean;
  onClose: () => void;
  layouts: LayoutDef[];
  categories: Record<string, string>;
  selectedLayoutId: string;
  onSelectLayout: (id: string) => void;
}

export const LayoutModal: React.FC<LayoutModalProps> = ({
  isOpen,
  onClose,
  layouts,
  categories,
  selectedLayoutId,
  onSelectLayout,
}) => {
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');

  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (isOpen && !dialog.open) dialog.showModal();
    if (!isOpen && dialog.open) dialog.close();
  }, [isOpen]);

  const items = layouts && layouts.length > 0 ? layouts : DEFAULT_CORE_LAYOUTS;

  const filtered = items.filter((l) => {
    const matchesCat = selectedCategory === 'all' || l.category === selectedCategory;
    const matchesSearch =
      l.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      l.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      l.id.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesCat && matchesSearch;
  });

  const implementedCount = items.filter((l) => l.implemented).length;
  const totalCount = items.length;

  return (
    <dialog ref={dialogRef} aria-labelledby="layout-catalog-title" onCancel={(event) => { event.preventDefault(); onClose(); }}
      className="fixed inset-0 m-auto max-h-[85dvh] w-[min(56rem,calc(100%-2rem))] max-w-none overflow-hidden rounded-xl border border-[var(--cf-line)] bg-[#17181e] p-0 text-gray-200 backdrop:bg-black/70">
      <div className="flex max-h-[85dvh] flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-[var(--cf-line)] px-4 py-3">
          <div>
            <h2 id="layout-catalog-title" className="text-lg font-semibold text-white">Layout catalog</h2>
            <p className="text-xs text-gray-400">{implementedCount} available · {totalCount} total · 9:16</p>
          </div>
          <button className="rounded-md p-2 text-gray-300 hover:bg-white/10" onClick={onClose} aria-label="Close layout catalog">
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        {/* Filter & Search Bar */}
        <div className="p-4 border-b border-[var(--cf-line)] bg-[var(--cf-base)] flex flex-col sm:flex-row gap-3 items-center justify-between">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              aria-pressed={selectedCategory === 'all'}
              onClick={() => setSelectedCategory('all')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                selectedCategory === 'all'
                  ? 'bg-[var(--cf-accent-soft)] text-white'
                  : 'bg-[var(--cf-raised)] text-gray-400 hover:text-gray-200 hover:bg-[#252a3b]'
              }`}
            >
              All ({totalCount})
            </button>
            {Object.entries(categories).map(([catKey, catLabel]) => {
              const count = layouts.filter((l) => l.category === catKey).length;
              return (
                <button
                  key={catKey}
                  aria-pressed={selectedCategory === catKey}
                  onClick={() => setSelectedCategory(catKey)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition ${
                    selectedCategory === catKey
                      ? 'bg-[var(--cf-accent-soft)] text-white'
                      : 'bg-[var(--cf-raised)] text-gray-400 hover:text-gray-200 hover:bg-[#252a3b]'
                  }`}
                >
                  {catLabel} ({count})
                </button>
              );
            })}
          </div>

          <div className="relative w-full sm:w-64">
            <Search size={15} className="absolute left-3 top-2.5 text-gray-500" />
            <input
              type="text"
              aria-label="Search layouts"
              placeholder="Search layouts…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-3 py-1.5 bg-[var(--cf-raised)] border border-[var(--cf-line)] rounded-lg text-xs text-white placeholder-gray-500 focus:outline-none focus:border-[var(--cf-accent)] transition"
            />
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-3 overflow-y-auto p-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.length === 0 && <p className="text-sm text-gray-400">No matching layouts.</p>}
          {filtered.map((layout) => (
            <button
              key={layout.id}
              type="button"
              disabled={!layout.implemented}
              aria-pressed={selectedLayoutId === layout.id}
              onClick={() => { onSelectLayout(layout.id); onClose(); }}
              className="rounded-lg border border-[var(--cf-line)] bg-[var(--cf-raised)] p-3 text-left enabled:hover:border-[var(--cf-accent)] aria-pressed:border-[var(--cf-accent)] disabled:opacity-60"
            >
              <span className="block break-words text-sm font-semibold text-white">{layout.name}</span>
              <span className="mt-1 block text-xs text-gray-300">{layout.description}</span>
              {!layout.implemented && <span className="mt-2 block text-xs text-gray-400">Unavailable</span>}
            </button>
          ))}
        </div>
      </div>
    </dialog>
  );
};
