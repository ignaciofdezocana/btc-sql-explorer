type Schema = Record<string, { column: string; type: string }[]>;

type Props = {
  schema: Schema | null;
  onClose: () => void;
};

export function SchemaModal({ schema, onClose }: Props) {
  if (schema === null) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-box" style={{ width: 520 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>Database Schema</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="modal-body">
          {Object.entries(schema).map(([table, cols]) => (
            <div key={table} style={{ marginBottom: 20 }}>
              <div className="schema-table-name">
                <span className="table-icon">T</span>
                {table}
              </div>
              <table className="schema-table">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                  </tr>
                </thead>
                <tbody>
                  {cols.map((c) => (
                    <tr key={c.column}>
                      <td style={{ fontWeight: 500 }}>{c.column}</td>
                      <td><code>{c.type}</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
