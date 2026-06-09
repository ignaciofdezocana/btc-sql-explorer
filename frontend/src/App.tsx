import { useState, useCallback, useRef, useEffect } from 'react';
import { SqlEditor, initialSql } from './components/SqlEditor';
import { ResultsTable } from './components/ResultsTable';
import { Sidebar } from './components/Sidebar';
import { SchemaModal } from './components/SchemaModal';
import { SaveQueryModal } from './components/SaveQueryModal';
import { EditQueryModal } from './components/EditQueryModal';
import { ChartConfigModal, ChartDisplayModal } from './components/ChartModals';
import {
  executeQuery,
  getSchema,
  saveQuery,
  updateSavedQuery,
  deleteSavedQuery,
  exportCsv,
  getSyncStatus,
  type ExecuteResult,
  type SchemaResponse,
  type SavedQuery,
  type ChartResponse,
  type SyncStatus,
} from './api/client';

/* ------------------------------------------------------------------ */
/*  Tab model                                                          */
/* ------------------------------------------------------------------ */

type StatusMsg = { type: 'running' | 'success' | 'error'; message: string };

type QueryTab = {
  id: string;
  title: string;
  sql: string;
  result: ExecuteResult | null;
  status: StatusMsg | null;
  running: boolean;
};

let _tabCounter = 0;
function createTab(sql?: string, title?: string): QueryTab {
  _tabCounter += 1;
  return {
    id: `tab-${Date.now()}-${_tabCounter}`,
    title: title || `Query ${_tabCounter}`,
    sql: sql ?? initialSql,
    result: null,
    status: null,
    running: false,
  };
}

/* ------------------------------------------------------------------ */
/*  Sync helpers                                                       */
/* ------------------------------------------------------------------ */

function formatEta(seconds: number): string {
  if (!seconds || seconds <= 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `~${h}h ${m}m`;
  if (m > 0) return `~${m}m`;
  return '<1m';
}

function formatTxRate(txPerSec: number): string {
  if (txPerSec >= 1_000_000) return `${(txPerSec / 1_000_000).toFixed(1)}M tx/s`;
  if (txPerSec >= 1_000) return `${(txPerSec / 1_000).toFixed(1)}K tx/s`;
  return `${Math.round(txPerSec)} tx/s`;
}

function SyncBanner({ status }: { status: SyncStatus }) {
  const state = status.state;
  let text = status.message || 'Syncing...';
  if (state === 'syncing') {
    // Lead with TRANSACTION-weighted progress \u2014 the honest measure of work
    // done. Block-based % is misleading: early blocks are nearly empty, so it
    // races to ~25% and then crawls through the dense middle of the chain,
    // looking "stuck". Block height is shown as secondary context.
    const txPct = status.tx_progress_pct ?? 0;
    const eta = (status.tx_eta_sec > 0) ? status.tx_eta_sec : status.eta_sec;
    text = `Syncing ${txPct.toFixed(1)}% of transactions \u00b7 block ${status.current_height?.toLocaleString()}/${status.tip_height?.toLocaleString()}`;
    if (status.tx_per_sec > 0) {
      text += ` \u00b7 ${formatTxRate(status.tx_per_sec)}`;
    } else if (status.blocks_per_sec > 0) {
      text += ` \u00b7 ${status.blocks_per_sec} blk/s`;
    }
    if (eta > 0) text += ` \u00b7 ${formatEta(eta)} left`;
  } else if (state === 'node_ibd') {
    text = `Bitcoin Core is syncing the blockchain (${(status.node_progress_pct ?? 0).toFixed(1)}%)`;
  } else if (state === 'waiting_for_node') {
    text = 'Connecting to Bitcoin Core node...';
  } else if (state === 'node_ready') {
    text = 'Preparing to sync...';
  }
  return (
    <div className="sync-banner">
      <span className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }} />
      <span>{text}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Chart type dropdown                                                */
/* ------------------------------------------------------------------ */

const CHART_TYPES = ['bar', 'line', 'area', 'scatter', 'pie'] as const;

function ChartDropdown({ onSelect, disabled }: { onSelect: (t: string) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
      >
        Chart
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ marginLeft: 2 }}>
          <path d="M2.5 4L5 6.5L7.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="dropdown-menu">
          {CHART_TYPES.map((t) => (
            <button
              key={t}
              type="button"
              className="dropdown-item"
              onClick={() => { onSelect(t); setOpen(false); }}
            >
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  App                                                                */
/* ------------------------------------------------------------------ */

export default function App() {
  /* ── Tab state ── */
  const [tabs, setTabs] = useState<QueryTab[]>(() => {
    const first = createTab();
    return [first];
  });
  const [activeTabId, setActiveTabId] = useState(() => tabs[0].id);

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? tabs[0];

  const updateTab = useCallback((id: string, patch: Partial<QueryTab>) => {
    setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }, []);

  /* ── Shared UI state ── */
  const [editorHeight, setEditorHeight] = useState(280);
  const [schema, setSchema] = useState<SchemaResponse['schema'] | null>(null);
  const [schemaModalOpen, setSchemaModalOpen] = useState(false);
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [editQuery, setEditQuery] = useState<SavedQuery | null>(null);
  const [savedVersion, setSavedVersion] = useState(0);
  const [chartConfigOpen, setChartConfigOpen] = useState(false);
  const [chartType, setChartType] = useState<string>('bar');
  const [chartDisplayResult, setChartDisplayResult] = useState<ChartResponse | null>(null);
  const [chartDisplayOpen, setChartDisplayOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);

  /* ── Per-tab refs for abort controllers & timers ── */
  const abortRefs = useRef<Map<string, AbortController>>(new Map());
  const timerRefs = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  /* ── Sync polling ── */
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const s = await getSyncStatus();
        if (active) setSyncStatus(s);
      } catch {
        // ignore
      }
    };
    poll();
    const id = setInterval(poll, 10_000);
    return () => { active = false; clearInterval(id); };
  }, []);

  /* ── Tab actions ── */

  const addTab = useCallback((sql?: string, title?: string) => {
    const tab = createTab(sql, title);
    setTabs((prev) => [...prev, tab]);
    setActiveTabId(tab.id);
  }, []);

  const closeTab = useCallback((tabId: string) => {
    setTabs((prev) => {
      if (prev.length <= 1) return prev;
      const idx = prev.findIndex((t) => t.id === tabId);
      const next = prev.filter((t) => t.id !== tabId);
      if (tabId === activeTabId) {
        const newIdx = Math.min(idx, next.length - 1);
        setActiveTabId(next[newIdx].id);
      }
      return next;
    });
    // Clean up any running query on the closed tab
    const ctrl = abortRefs.current.get(tabId);
    if (ctrl) { ctrl.abort(); abortRefs.current.delete(tabId); }
    const timer = timerRefs.current.get(tabId);
    if (timer) { clearInterval(timer); timerRefs.current.delete(tabId); }
  }, [activeTabId]);

  /* ── Run query (on active tab) ── */

  const runQuery = useCallback(async () => {
    const tab = tabs.find((t) => t.id === activeTabId);
    if (!tab || tab.running || !tab.sql.trim()) return;

    const tabId = tab.id;
    updateTab(tabId, { running: true, status: { type: 'running', message: 'Executing...' }, result: null });

    const controller = new AbortController();
    abortRefs.current.set(tabId, controller);

    let seconds = 0;
    const timer = setInterval(() => {
      seconds += 1;
      updateTab(tabId, { status: { type: 'running', message: `Executing... ${seconds}s` } });
    }, 1000);
    timerRefs.current.set(tabId, timer);

    try {
      const data = await executeQuery(tab.sql.trim(), controller.signal);
      clearInterval(timer);
      timerRefs.current.delete(tabId);
      updateTab(tabId, {
        result: data,
        running: false,
        status: {
          type: 'success',
          message: `${data.row_count.toLocaleString()} rows \u00b7 ${data.column_count} cols \u00b7 ${data.execution_time.toFixed(3)}s`,
        },
      });
      setTimeout(() => {
        setTabs((prev) =>
          prev.map((t) => (t.id === tabId && t.status?.type === 'success' ? { ...t, status: null } : t)),
        );
      }, 5000);
    } catch (err) {
      clearInterval(timer);
      timerRefs.current.delete(tabId);
      if (controller.signal.aborted) {
        updateTab(tabId, { running: false, status: { type: 'error', message: 'Query cancelled' }, result: null });
        setTimeout(() => {
          setTabs((prev) =>
            prev.map((t) => (t.id === tabId && t.status?.message === 'Query cancelled' ? { ...t, status: null } : t)),
          );
        }, 3000);
      } else {
        updateTab(tabId, {
          running: false,
          status: { type: 'error', message: err instanceof Error ? err.message : 'Query failed' },
          result: null,
        });
      }
    } finally {
      abortRefs.current.delete(tabId);
    }
  }, [tabs, activeTabId, updateTab]);

  /* Cancel query */
  const cancelQuery = useCallback(() => {
    const ctrl = abortRefs.current.get(activeTabId);
    if (ctrl) ctrl.abort();
  }, [activeTabId]);

  /* Schema */
  const showSchema = useCallback(async () => {
    try {
      const res = await getSchema();
      setSchema(res.schema);
      setSchemaModalOpen(true);
    } catch {
      updateTab(activeTabId, { status: { type: 'error', message: 'Could not load schema' } });
    }
  }, [activeTabId, updateTab]);

  /* Save / edit / delete */
  const handleSaveQuery = useCallback(async (name: string, description: string) => {
    await saveQuery({ name, description, query: activeTab.sql.trim() });
    setSavedVersion((v) => v + 1);
  }, [activeTab.sql]);

  const handleUpdateQuery = useCallback(async (id: number, name: string, description: string, query: string) => {
    await updateSavedQuery(id, { name, description, query });
    setSavedVersion((v) => v + 1);
    updateTab(activeTabId, { sql: query });
    setEditQuery(null);
  }, [activeTabId, updateTab]);

  const handleDeleteQuery = useCallback(async (id: number) => {
    await deleteSavedQuery(id);
    setSavedVersion((v) => v + 1);
    setEditQuery(null);
  }, []);

  /* Export */
  const handleExport = useCallback(async () => {
    if (!activeTab.result?.data?.length) return;
    const name = `btc_query_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.csv`;
    const blob = await exportCsv(activeTab.result.data, name);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }, [activeTab.result]);

  /* Chart */
  const openChartConfig = (type: string) => {
    setChartType(type);
    setChartConfigOpen(true);
  };

  const hasResults = activeTab.result && activeTab.result.data.length > 0 && activeTab.result.columns.length > 0;

  /* Sidebar → load query into current tab */
  const loadQuery = useCallback((query: string, title?: string) => {
    updateTab(activeTabId, {
      sql: query,
      result: null,
      status: null,
      ...(title ? { title } : {}),
    });
  }, [activeTabId, updateTab]);

  /* Sync banner */
  const showSyncBanner = syncStatus && syncStatus.syncing && (
    syncStatus.state === 'syncing' ||
    syncStatus.state === 'waiting_for_node' ||
    syncStatus.state === 'node_ibd' ||
    syncStatus.state === 'node_ready'
  );

  /* ---------------------------------------------------------------- */
  return (
    <div className="app">
      {/* ── Top bar ── */}
      <div className="topbar">
        <button
          type="button"
          className="topbar-btn sidebar-toggle"
          onClick={() => setSidebarOpen((o) => !o)}
          title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          aria-label="Toggle sidebar"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />
          </svg>
        </button>
        <div className="topbar-brand">
          <span className="btc-icon">{'\u20BF'}</span>
          <span>BTC SQL Explorer</span>
        </div>
        <div className="topbar-separator" />
        <span className="topbar-subtitle">Query the Bitcoin blockchain with SQL</span>
        <div className="topbar-spacer" />
        <div className="topbar-actions">
          <button type="button" className="topbar-btn" onClick={showSchema}>
            Schema
          </button>
          <a
            className="topbar-btn"
            href="/api/logs/download"
            title="Download a diagnostics bundle (logs + system snapshot) to share when the sync gets stuck"
          >
            Diagnostics
          </a>
        </div>
      </div>

      {/* ── Sync banner ── */}
      {showSyncBanner && <SyncBanner status={syncStatus} />}

      {/* ── Body ── */}
      <div className="app-body">
        {/* Sidebar */}
        <div className={`sidebar-container ${sidebarOpen ? 'open' : 'closed'}`}>
          <Sidebar
            onSelectQuery={loadQuery}
            onEditSaved={(query) => setEditQuery(query)}
            refreshSavedTrigger={savedVersion}
          />
        </div>

        {/* Main */}
        <div className="main-panel">
          {/* ── Tab bar ── */}
          <div className="query-tabs">
            <div className="query-tabs-list">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  className={`query-tab ${tab.id === activeTabId ? 'active' : ''}`}
                  onClick={() => setActiveTabId(tab.id)}
                  onMouseDown={(e) => {
                    // Middle-click to close
                    if (e.button === 1 && tabs.length > 1) {
                      e.preventDefault();
                      closeTab(tab.id);
                    }
                  }}
                  title={tab.title}
                >
                  {tab.running && (
                    <span className="spinner" style={{ width: 10, height: 10, borderWidth: 1.5 }} />
                  )}
                  <span className="query-tab-title">{tab.title}</span>
                  {tabs.length > 1 && (
                    <span
                      className="query-tab-close"
                      onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
                      title="Close tab"
                    >
                      <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                        <path d="M2.5 2.5l5 5M7.5 2.5l-5 5" />
                      </svg>
                    </span>
                  )}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="query-tab-add"
              onClick={() => addTab()}
              title="New query tab"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M6 2v8M2 6h8" />
              </svg>
            </button>
          </div>

          {/* Toolbar */}
          <div className="toolbar">
            <div className="toolbar-group">
              {activeTab.running ? (
                <button
                  type="button"
                  className="btn btn-danger-outline btn-sm"
                  onClick={cancelQuery}
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <rect x="2.5" y="2.5" width="7" height="7" rx="1" fill="currentColor" />
                  </svg>
                  Cancel
                </button>
              ) : (
                <button
                  type="button"
                  className="btn btn-run btn-sm"
                  onClick={runQuery}
                  disabled={!activeTab.sql.trim()}
                >
                  <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                    <path d="M3 2L10 6L3 10V2Z" fill="currentColor" />
                  </svg>
                  Run
                </button>
              )}
            </div>

            <div className="toolbar-separator" />

            <div className="toolbar-group">
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setSaveModalOpen(true)}
                disabled={!activeTab.sql.trim()}
              >
                Save
              </button>
              <ChartDropdown onSelect={openChartConfig} disabled={!hasResults} />
              {hasResults && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={handleExport}>
                  Export
                </button>
              )}
            </div>

            <div className="toolbar-spacer" />

            {activeTab.status && (
              <div className={`query-status ${activeTab.status.type}`} style={{ margin: 0, padding: '4px 10px', fontSize: '11.5px' }}>
                {activeTab.status.type === 'running' && <span className="spinner" style={{ width: 12, height: 12, borderWidth: 1.5 }} />}
                {activeTab.status.message}
              </div>
            )}

            {!activeTab.status && (
              <div className="toolbar-hint">
                <kbd>{navigator.platform?.includes('Mac') ? '\u2318' : 'Ctrl'}</kbd>
                <span>+</span>
                <kbd>{'\u21B5'}</kbd>
                <span>to run</span>
              </div>
            )}
          </div>

          {/* Editor */}
          <div className="editor-section" style={{ height: editorHeight }}>
            <SqlEditor
              key={activeTabId}
              value={activeTab.sql}
              onChange={(v) => updateTab(activeTabId, { sql: v })}
              onExecute={runQuery}
              height={editorHeight}
              onHeightChange={setEditorHeight}
              disabled={activeTab.running}
            />
          </div>

          {/* Resize handle */}
          <div
            className="resize-handle"
            onMouseDown={(e) => {
              e.preventDefault();
              const startY = e.clientY;
              const startH = editorHeight;
              const onMove = (ev: MouseEvent) => {
                const delta = ev.clientY - startY;
                setEditorHeight(Math.max(120, Math.min(600, startH + delta)));
              };
              const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
              };
              document.body.style.cursor = 'row-resize';
              document.body.style.userSelect = 'none';
              document.addEventListener('mousemove', onMove);
              document.addEventListener('mouseup', onUp);
            }}
          />

          {/* Results */}
          <div className="results-section">
            <div className="results-header">
              <span className="results-header-title">Results</span>
              {activeTab.result && (
                <div className="results-meta">
                  <span>{activeTab.result.row_count.toLocaleString()} rows</span>
                  <span className="dot" />
                  <span>{activeTab.result.column_count} columns</span>
                  {activeTab.result.execution_time != null && (
                    <>
                      <span className="dot" />
                      <span>{activeTab.result.execution_time.toFixed(3)}s</span>
                    </>
                  )}
                </div>
              )}
            </div>
            <ResultsTable
              columns={activeTab.result?.columns ?? []}
              data={activeTab.result?.data ?? []}
            />
          </div>
        </div>
      </div>

      {/* ── Modals ── */}
      {schemaModalOpen && <SchemaModal schema={schema} onClose={() => setSchemaModalOpen(false)} />}
      {saveModalOpen && (
        <SaveQueryModal query={activeTab.sql} onSave={handleSaveQuery} onClose={() => setSaveModalOpen(false)} />
      )}
      {editQuery && (
        <EditQueryModal
          query={editQuery}
          onUpdate={handleUpdateQuery}
          onDelete={handleDeleteQuery}
          onClose={() => setEditQuery(null)}
        />
      )}
      {chartConfigOpen && hasResults && (
        <ChartConfigModal
          columns={activeTab.result!.columns}
          chartType={chartType}
          data={activeTab.result!.data}
          onClose={() => setChartConfigOpen(false)}
          onCreated={(res) => {
            setChartDisplayResult(res);
            setChartDisplayOpen(true);
          }}
        />
      )}
      {chartDisplayOpen && chartDisplayResult && (
        <ChartDisplayModal
          chartResult={chartDisplayResult}
          chartType={chartType}
          onClose={() => { setChartDisplayOpen(false); setChartDisplayResult(null); }}
        />
      )}
    </div>
  );
}
