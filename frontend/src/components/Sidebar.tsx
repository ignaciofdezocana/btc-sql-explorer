import { useState, useEffect, useMemo } from 'react';
import { getExamples, getSavedQueries, type ExamplesMap, type SavedQuery } from '../api/client';

type Props = {
  onSelectQuery: (query: string, title?: string) => void;
  onEditSaved: (query: SavedQuery) => void;
  refreshSavedTrigger?: number;
};

export function Sidebar({ onSelectQuery, onEditSaved, refreshSavedTrigger }: Props) {
  const [examples, setExamples] = useState<ExamplesMap | null>(null);
  const [saved, setSaved] = useState<SavedQuery[]>([]);
  const [search, setSearch] = useState('');
  // Tracks manual expand/collapse. `true` = expanded, `false` = collapsed.
  // Groups not present here use the default (collapsed when no search, expanded when search matches).
  const [manualExpanded, setManualExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    getExamples().then((r) => setExamples(r.examples)).catch(() => setExamples({}));
  }, []);

  useEffect(() => {
    getSavedQueries().then((r) => setSaved(r.queries)).catch(() => setSaved([]));
  }, [refreshSavedTrigger]);

  const term = search.toLowerCase();
  const isSearching = term.length > 0;

  const filteredSaved = useMemo(
    () =>
      saved.filter(
        (q) =>
          !term ||
          q.name.toLowerCase().includes(term) ||
          (q.description && q.description.toLowerCase().includes(term))
      ),
    [saved, term]
  );

  const toggleGroup = (name: string) => {
    setManualExpanded((prev) => ({ ...prev, [name]: !isGroupOpen(name) }));
  };

  // Determine if a group should be open:
  // - If user manually toggled it, respect that
  // - If searching and the group has matches, expand it
  // - Otherwise, collapsed
  const isGroupOpen = (name: string): boolean => {
    if (name in manualExpanded) return manualExpanded[name];
    if (isSearching) return true; // auto-expand groups with matches during search
    return false; // collapsed by default
  };

  // Clear manual overrides when search term changes so auto-expand kicks in
  useEffect(() => {
    setManualExpanded({});
  }, [search]);

  return (
    <div className="sidebar">
      {/* Header */}
      <div style={{ padding: '12px 12px 0', flexShrink: 0 }}>
        <div
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--text-secondary)',
            textTransform: 'uppercase',
            letterSpacing: '0.04em',
            marginBottom: 8,
          }}
        >
          Query Library
        </div>
      </div>

      {/* Search */}
      <div className="sidebar-search">
        <input
          type="text"
          placeholder="Search queries..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Content */}
      <div className="sidebar-content">
        {/* My Queries */}
        {(filteredSaved.length > 0 || (!isSearching && saved.length === 0)) && (
          <div>
            <div className="sidebar-group-header" onClick={() => toggleGroup('__saved')}>
              <span>My Queries</span>
              <span className={`chevron ${isGroupOpen('__saved') ? 'open' : ''}`}>
                {'\u25B6'}
              </span>
            </div>

            {isGroupOpen('__saved') && (
              <>
                {filteredSaved.length === 0 ? (
                  <div className="sidebar-empty" style={{ padding: '12px 20px' }}>
                    <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                      Save a query to see it here
                    </span>
                  </div>
                ) : (
                  filteredSaved.map((q) => (
                    <div key={q.id} className="sidebar-item">
                      <div onClick={() => onSelectQuery(q.query, q.name)} style={{ cursor: 'pointer' }}>
                        <div className="sidebar-item-title">{q.name}</div>
                        {q.description && (
                          <div className="sidebar-item-desc">{q.description}</div>
                        )}
                      </div>
                      <div className="sidebar-item-actions">
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs"
                          onClick={(e) => {
                            e.stopPropagation();
                            onEditSaved(q);
                          }}
                        >
                          Edit
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </>
            )}
          </div>
        )}

        {/* Example categories */}
        {examples &&
          Object.entries(examples).map(([catName, cat]) => {
            const qs = Object.entries(cat.queries).filter(
              ([qName, q]) =>
                !term ||
                qName.toLowerCase().includes(term) ||
                q.description.toLowerCase().includes(term)
            );
            if (qs.length === 0) return null;
            const open = isGroupOpen(catName);
            return (
              <div key={catName}>
                <div className="sidebar-group-header" onClick={() => toggleGroup(catName)}>
                  <span>{catName}</span>
                  <span className={`chevron ${open ? 'open' : ''}`}>{'\u25B6'}</span>
                </div>
                {open &&
                  qs.map(([qName, q]) => (
                    <div
                      key={qName}
                      className="sidebar-item"
                      onClick={() => onSelectQuery(q.query.trim(), qName)}
                    >
                      <div className="sidebar-item-title">{qName}</div>
                      <div className="sidebar-item-desc">{q.description}</div>
                    </div>
                  ))}
              </div>
            );
          })}
      </div>
    </div>
  );
}
