import { useState } from 'react';
import Plot from 'react-plotly.js';
import { createChart, type ChartResponse } from '../api/client';

type ConfigModalProps = {
  columns: string[];
  chartType: string;
  onClose: () => void;
  onCreated: (result: ChartResponse) => void;
  data: Record<string, unknown>[];
};

export function ChartConfigModal({ columns, chartType, onClose, onCreated, data }: ConfigModalProps) {
  const [xColumn, setXColumn] = useState(columns[0] || '');
  const [yColumn, setYColumn] = useState(columns[1] || '');
  const [pieValue, setPieValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const isPie = chartType === 'pie';

  const handleCreate = async () => {
    if (!xColumn) return;
    if (!isPie && !yColumn) return;
    setError('');
    setLoading(true);
    try {
      const result = await createChart({
        data,
        chart_type: chartType,
        x_column: xColumn,
        y_column: isPie ? pieValue : yColumn,
      });
      onClose();
      onCreated(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Chart failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-box" style={{ width: 380 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>
            {chartType.charAt(0).toUpperCase() + chartType.slice(1)} Chart
          </h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="modal-body">
          <div style={{ marginBottom: 14 }}>
            <label className="label">{isPie ? 'Labels column' : 'X-axis column'} *</label>
            <select className="input-sm" value={xColumn} onChange={(e) => setXColumn(e.target.value)}>
              <option value="">Select...</option>
              {columns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          {!isPie && (
            <div style={{ marginBottom: 14 }}>
              <label className="label">Y-axis column *</label>
              <select className="input-sm" value={yColumn} onChange={(e) => setYColumn(e.target.value)}>
                <option value="">Select...</option>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          )}
          {isPie && (
            <div style={{ marginBottom: 14 }}>
              <label className="label">Value column (optional)</label>
              <select className="input-sm" value={pieValue} onChange={(e) => setPieValue(e.target.value)}>
                <option value="">Count occurrences</option>
                {columns.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          )}
          {error && <div className="query-status error">{error}</div>}
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-outline" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleCreate}
            disabled={loading || !xColumn || (!isPie && !yColumn)}
          >
            {loading ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */

type DisplayModalProps = {
  chartResult: ChartResponse | null;
  chartType: string;
  onClose: () => void;
};

export function ChartDisplayModal({ chartResult, chartType, onClose }: DisplayModalProps) {
  if (!chartResult) return null;

  type PlotlyParsed = { data: object[]; layout: object };
  let plotData: PlotlyParsed | null = null;
  try {
    const parsed = JSON.parse(chartResult.chart_json) as PlotlyParsed;
    plotData = { data: parsed.data, layout: parsed.layout };
  } catch {
    return (
      <div className="modal-backdrop" onClick={onClose}>
        <div className="modal-box" onClick={(e) => e.stopPropagation()}>
          <div className="modal-body">
            <div className="query-status error">Failed to parse chart data.</div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-outline" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-box"
        style={{ width: '90vw', maxWidth: 960, height: '80vh' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>
            {chartType.charAt(0).toUpperCase() + chartType.slice(1)} Chart
          </h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="modal-body" style={{ flex: 1, minHeight: 300 }}>
          <Plot
            data={plotData.data}
            layout={{
              ...plotData.layout,
              autosize: true,
              height: 480,
              margin: { l: 50, r: 30, t: 40, b: 60 },
              font: { family: 'Inter, system-ui, sans-serif', size: 12 },
            }}
            useResizeHandler
            style={{ width: '100%', height: '100%' }}
            config={{ displayModeBar: true, displaylogo: false, responsive: true }}
          />
        </div>
        <div className="modal-footer">
          <span style={{ color: 'var(--muted)', fontSize: 11.5, marginRight: 'auto' }}>
            {chartResult.data_points} data points
          </span>
          <button type="button" className="btn btn-outline" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
