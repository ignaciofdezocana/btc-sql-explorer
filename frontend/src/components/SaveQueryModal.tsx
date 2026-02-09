import { useState } from 'react';

type Props = {
  query: string;
  onSave: (name: string, description: string) => Promise<void>;
  onClose: () => void;
};

export function SaveQueryModal({ query, onSave, onClose }: Props) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setError('');
    setSaving(true);
    try {
      await onSave(name.trim(), description.trim());
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-box" style={{ width: 440 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>Save Query</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {error && <div className="query-status error" style={{ marginBottom: 12 }}>{error}</div>}
            <div style={{ marginBottom: 14 }}>
              <label className="label">Name *</label>
              <input
                type="text"
                className="input-sm"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Top blocks by tx count"
                autoFocus
                required
              />
            </div>
            <div style={{ marginBottom: 14 }}>
              <label className="label">Description</label>
              <input
                type="text"
                className="input-sm"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Optional short description"
              />
            </div>
            <div>
              <label className="label">Query</label>
              <pre
                style={{
                  background: 'var(--surface-1)',
                  border: '1px solid var(--line)',
                  borderRadius: 'var(--radius-sm)',
                  padding: 10,
                  fontSize: 11.5,
                  maxHeight: 140,
                  overflow: 'auto',
                  margin: 0,
                  fontFamily: 'ui-monospace, monospace',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  color: 'var(--text-secondary)',
                }}
              >
                {query}
              </pre>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-outline" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving || !name.trim()}>
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
