import { useState, useEffect } from 'react';
import type { SavedQuery } from '../api/client';

type Props = {
  query: SavedQuery | null;
  onUpdate: (id: number, name: string, description: string, query: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onClose: () => void;
};

export function EditQueryModal({ query, onUpdate, onDelete, onClose }: Props) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [sql, setSql] = useState('');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (query) {
      setName(query.name);
      setDescription(query.description || '');
      setSql(query.query);
      setError('');
    }
  }, [query]);

  if (!query) return null;

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !sql.trim()) return;
    setError('');
    setSaving(true);
    try {
      await onUpdate(query.id, name.trim(), description.trim(), sql.trim());
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete "${query.name}"?`)) return;
    setDeleting(true);
    try {
      await onDelete(query.id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-box" style={{ width: 520 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3 style={{ margin: 0 }}>Edit Query</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <form onSubmit={handleUpdate}>
          <div className="modal-body">
            {error && <div className="query-status error" style={{ marginBottom: 12 }}>{error}</div>}
            <div style={{ marginBottom: 14 }}>
              <label className="label">Name *</label>
              <input
                type="text"
                className="input-sm"
                value={name}
                onChange={(e) => setName(e.target.value)}
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
              />
            </div>
            <div>
              <label className="label">Query</label>
              <textarea
                className="input-sm"
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                rows={10}
                style={{ fontFamily: 'ui-monospace, monospace', fontSize: 12.5 }}
                required
              />
            </div>
          </div>
          <div className="modal-footer">
            <button
              type="button"
              className="btn btn-danger-outline btn-sm"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </button>
            <div style={{ flex: 1 }} />
            <button type="button" className="btn btn-outline" onClick={onClose}>Cancel</button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={saving || !name.trim() || !sql.trim()}
            >
              {saving ? 'Saving...' : 'Update'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
