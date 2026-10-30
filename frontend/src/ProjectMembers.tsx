import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getAuthToken, patch, post } from './api';

type Member = { user_id: string; username: string; role: 'viewer' | 'analyst' | 'admin'; active: boolean };

export default function ProjectMembers() {
  const qc = useQueryClient();
  const enabled = !!getAuthToken();
  const project = useQuery({ queryKey: ['project'], queryFn: () => api<{ id: string }>('/project'), enabled });
  const me = useQuery({ queryKey: ['auth-me'], queryFn: () => api<{ role: string }>('/auth/me'), enabled });
  const members = useQuery({ queryKey: ['members', project.data?.id], queryFn: () => api<Member[]>(`/projects/${project.data?.id}/members`), enabled: !!project.data?.id });
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Member['role']>('viewer');
  const [error, setError] = useState('');
  if (!enabled) return null;

  async function add() {
    try {
      setError('');
      await post(`/projects/${project.data?.id}/members`, { username, password, role });
      setUsername(''); setPassword('');
      qc.invalidateQueries({ queryKey: ['members', project.data?.id] });
    } catch (exc) { setError(String(exc)); }
  }

  async function change(userId: string, nextRole: Member['role']) {
    try {
      setError('');
      await patch(`/projects/${project.data?.id}/members/${userId}`, { role: nextRole });
      qc.invalidateQueries({ queryKey: ['members', project.data?.id] });
    } catch (exc) { setError(String(exc)); }
  }

  return <div className="card"><h2>Project members</h2>{error && <p className="alert error">{error}</p>}{members.data?.map((item) => <div className="list-row" key={item.user_id}><span>{item.username}</span><span>{item.role}</span>{me.data?.role === 'admin' && <select value={item.role} onChange={(event) => change(item.user_id, event.target.value as Member['role'])}><option value="viewer">Viewer</option><option value="analyst">Analyst</option><option value="admin">Administrator</option></select>}</div>)}{me.data?.role === 'admin' && <div className="field-grid"><label>New username<input value={username} onChange={(event) => setUsername(event.target.value)} /></label><label>Temporary password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label><label>Role<select value={role} onChange={(event) => setRole(event.target.value as Member['role'])}><option value="viewer">Viewer</option><option value="analyst">Analyst</option><option value="admin">Administrator</option></select></label><button disabled={!username || password.length < 10} onClick={add}>Add member</button></div>}</div>;
}
