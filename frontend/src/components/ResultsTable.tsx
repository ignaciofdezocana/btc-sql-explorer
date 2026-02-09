import { useCallback, useState } from 'react';

type Props = {
  columns: string[];
  data: Record<string, unknown>[];
};

function formatCell(value: unknown, column: string): { display: string; full: string; className: string } {
  const colLower = column.toLowerCase();
  const full = value == null ? 'null' : String(value);
  let display = full;
  let className = '';

  if (value === null || value === undefined) {
    return { display: 'null', full: 'null', className: 'cell-num' };
  }

  if (colLower.includes('hash') || colLower.includes('address') || colLower.includes('root')) {
    className = 'cell-hash';
    if (full.length > 20) display = full.slice(0, 8) + '\u2026' + full.slice(-6);
  } else if (typeof value === 'number' || (typeof value === 'string' && value !== '' && !isNaN(Number(value)))) {
    className = 'cell-num';
    if (typeof value === 'number' && Number.isInteger(value) && Math.abs(value) >= 1000) {
      display = value.toLocaleString();
    }
  } else if (full.length > 40) {
    display = full.slice(0, 38) + '\u2026';
  }

  return { display, full, className };
}

export function ResultsTable({ columns, data }: Props) {
  const [copyToast, setCopyToast] = useState(false);
  const [tooltip, setTooltip] = useState<{ text: string; x: number; y: number } | null>(null);

  const copy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopyToast(true);
      setTimeout(() => setCopyToast(false), 1500);
    });
  }, []);

  if (!columns.length) {
    return (
      <div className="results-wrap" style={{ display: 'flex' }}>
        <div className="empty-state">
          <div className="empty-state-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M4 7V4h16v3" /><path d="M9 20h6" /><path d="M12 4v16" />
            </svg>
          </div>
          <div className="empty-state-title">No results yet</div>
          <div className="empty-state-desc">Write a SQL query and press Run to see results</div>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="results-wrap">
        <table className="results-table">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => {
                  const { display, full, className } = formatCell(row[col], col);
                  const isTruncated = display !== full && full !== 'null';
                  return (
                    <td
                      key={col}
                      className={className}
                      onClick={() => copy(full)}
                      onMouseEnter={(e) => {
                        if (isTruncated) {
                          const rect = e.currentTarget.getBoundingClientRect();
                          setTooltip({ text: full, x: rect.left, y: rect.bottom + 4 });
                        }
                      }}
                      onMouseLeave={() => setTooltip(null)}
                    >
                      {full === 'null' ? <em style={{ color: 'var(--muted)', fontStyle: 'normal', opacity: 0.5 }}>NULL</em> : display}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {tooltip && (
        <div
          style={{
            position: 'fixed',
            left: tooltip.x,
            top: tooltip.y,
            maxWidth: 420,
            wordBreak: 'break-all',
            background: '#1e293b',
            color: '#f1f5f9',
            padding: '6px 10px',
            borderRadius: 6,
            fontSize: 11,
            zIndex: 1500,
            pointerEvents: 'none',
            boxShadow: '0 4px 12px rgba(0,0,0,.2)',
            fontFamily: 'ui-monospace, monospace',
          }}
        >
          {tooltip.text}
        </div>
      )}

      {copyToast && (
        <div className="toast-copy">Copied!</div>
      )}
    </>
  );
}
