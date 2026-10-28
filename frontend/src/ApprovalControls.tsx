import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getAuthToken, patch, post } from './api';

type Approval = { id: string; status: string; note: string; created_at: string };

export default function ApprovalControls({ artifactId, kind }: { artifactId: string; kind: string }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const enabled = ['redesign', 'migration_proposal', 'relationship_suggestions'].includes(kind);
  const approvals = useQuery({ queryKey: ['approvals', artifactId], queryFn: () => api<Approval[]>(`/change-approvals?artifact_id=${artifactId}`), enabled });
  const me = useQuery({ queryKey: ['auth-me'], queryFn: () => api<{ role: string }>('/auth/me'), enabled: !!getAuthToken() });
  const canReview = !getAuthToken() || me.data?.role === 'admin';
  if (!enabled) return null;

  async function create() {
    try {
      setError('');
      await post('/change-approvals', { artifact_id: artifactId, note });
      setNote('');
      qc.invalidateQueries({ queryKey: ['approvals', artifactId] });
    } catch (exc) { setError(String(exc)); }
  }

  async function review(id: string, status: 'approved' | 'rejected') {
    try {
      setError('');
      await patch(`/change-approvals/${id}`, { status, note });
      qc.invalidateQueries({ queryKey: ['approvals', artifactId] });
    } catch (exc) { setError(String(exc)); }
  }

  return <section><h3>Proposal review</h3><p className="muted">This records a local review decision; it never applies database changes.</p>{error && <p className="alert error">{error}</p>}{canReview && <div className="actions"><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Review note" /><button className="secondary" onClick={create}>Request review</button></div>}{approvals.data?.map((item) => <div className="list-row" key={item.id}><span>{item.status}</span><span>{item.note}</span>{canReview && item.status === 'pending' && <div className="actions"><button className="quiet" onClick={() => review(item.id, 'approved')}>Approve</button><button className="quiet" onClick={() => review(item.id, 'rejected')}>Reject</button></div>}</div>)}</section>;
}
