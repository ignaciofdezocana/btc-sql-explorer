const API_BASE = '';

export type ExecuteResult = {
  success: true;
  data: Record<string, unknown>[];
  columns: string[];
  row_count: number;
  column_count: number;
  execution_time: number;
  message: string;
};

export type SchemaResponse = { success: true; schema: Record<string, { column: string; type: string }[]> };
export type StatsResponse = { success: true; stats: { total_blocks: number; total_transactions: number; total_inputs: number; total_outputs: number; first_block: number; last_block: number } };
export type ExamplesResponse = { success: true; examples: ExamplesMap };
export type SavedQueriesResponse = { success: true; queries: SavedQuery[] };
export type SavedQuery = { id: number; name: string; description: string; query: string; created_at: string | null; updated_at: string | null };
export type ChartResponse = { success: true; chart_json: string; data_points: number; chart_type: string };

export type SyncStatus = {
  state: string;
  message: string;
  current_height: number;
  tip_height: number;
  db_blocks: number;
  progress_pct: number;
  blocks_per_sec: number;
  eta_sec: number;
  elapsed_sec: number;
  // Transaction-weighted progress (more accurate than block-based)
  tx_progress_pct: number;
  tx_per_sec: number;
  tx_eta_sec: number;
  tx_synced: number;
  node_progress_pct: number | null;
  node_blocks: number | null;
  node_headers: number | null;
  syncing: boolean;
  updated_at: string | null;
};

export type ExamplesMap = Record<string, {
  description: string;
  queries: Record<string, { description: string; query: string }>;
}>;

async function handleRes<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error || `HTTP ${res.status}`);
  return data as T;
}

export async function executeQuery(query: string, signal?: AbortSignal): Promise<ExecuteResult> {
  const res = await fetch(`${API_BASE}/api/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: query.trim() }),
    signal,
  });
  return handleRes<ExecuteResult>(res);
}

export async function getSchema(): Promise<SchemaResponse> {
  const res = await fetch(`${API_BASE}/api/schema`);
  return handleRes<SchemaResponse>(res);
}

export async function getStats(): Promise<StatsResponse> {
  const res = await fetch(`${API_BASE}/api/stats`);
  return handleRes<StatsResponse>(res);
}

export async function getExamples(): Promise<ExamplesResponse> {
  const res = await fetch(`${API_BASE}/api/examples`);
  return handleRes<ExamplesResponse>(res);
}

export async function getSavedQueries(): Promise<SavedQueriesResponse> {
  const res = await fetch(`${API_BASE}/api/saved-queries`);
  return handleRes<SavedQueriesResponse>(res);
}

export async function saveQuery(payload: { name: string; description: string; query: string }): Promise<{ success: true; message: string; id: number }> {
  const res = await fetch(`${API_BASE}/api/saved-queries`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handleRes(res);
}

export async function updateSavedQuery(id: number, payload: { name: string; description: string; query: string }): Promise<{ success: true; message: string }> {
  const res = await fetch(`${API_BASE}/api/saved-queries/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handleRes(res);
}

export async function deleteSavedQuery(id: number): Promise<{ success: true; message: string }> {
  const res = await fetch(`${API_BASE}/api/saved-queries/${id}`, { method: 'DELETE' });
  return handleRes(res);
}

export async function exportCsv(data: Record<string, unknown>[], filename: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/api/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data, filename }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { error?: string }).error || 'Export failed');
  }
  return res.blob();
}

export async function getSyncStatus(): Promise<SyncStatus> {
  const res = await fetch(`${API_BASE}/api/sync-status`);
  return res.json();
}

export async function createChart(payload: { data: Record<string, unknown>[]; chart_type: string; x_column: string; y_column: string }): Promise<ChartResponse> {
  const res = await fetch(`${API_BASE}/api/chart`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return handleRes<ChartResponse>(res);
}
