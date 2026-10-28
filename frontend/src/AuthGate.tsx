import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import App from './App';
import { api, clearAuthToken, getAuthToken, post, setAuthToken } from './api';

type AuthStatus = { required: boolean; bootstrap_needed: boolean };
type LoginResponse = { access_token: string; user: { username: string; role: string } };

export default function AuthGate() {
  const qc = useQueryClient();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [authorized, setAuthorized] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [bootstrapSecret, setBootstrapSecret] = useState('');
  const [identity, setIdentity] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    api<AuthStatus>('/auth/status').then(async (next) => {
      if (!active) return;
      setStatus(next);
      if (!next.required) { setAuthorized(true); return; }
      if (!getAuthToken()) return;
      try {
        const user = await api<{ username: string; role: string }>('/auth/me');
        if (active) { setIdentity(`${user.username} · ${user.role}`); setAuthorized(true); }
      } catch { clearAuthToken(); }
    }).catch((exc) => { if (active) setError(String(exc)); });
    return () => { active = false; };
  }, []);

  async function submit() {
    setError('');
    try {
      if (status?.bootstrap_needed) {
        const response = await fetch('/api/v1/auth/bootstrap', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Bootstrap-Secret': bootstrapSecret }, body: JSON.stringify({ username, password }) });
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.message || `Bootstrap failed (${response.status})`);
        }
        setStatus({ required: true, bootstrap_needed: false });
      }
      const result = await post<LoginResponse>('/auth/login', { username, password });
      setAuthToken(result.access_token);
      setIdentity(`${result.user.username} · ${result.user.role}`);
      setPassword('');
      setBootstrapSecret('');
      setAuthorized(true);
    } catch (exc) { setError(String(exc)); }
  }

  async function logout() {
    try { await post('/auth/logout', {}); } catch { /* Clear the local token even if the server is unavailable. */ }
    clearAuthToken();
    qc.clear();
    setAuthorized(false);
    setIdentity('');
  }

  if (!status) return <div className="auth-screen"><div className="card">{error || 'Loading workspace…'}</div></div>;
  if (!authorized) return <div className="auth-screen"><div className="card auth-card"><h1>{status.bootstrap_needed ? 'Create first administrator' : 'Sign in'}</h1><p>{status.bootstrap_needed ? 'Enable shared-project mode by creating its first administrator.' : 'Access the database workbench.'}</p>{error && <p className="alert error">{error}</p>}<label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} /></label><label>Password<input type="password" autoComplete={status.bootstrap_needed ? 'new-password' : 'current-password'} value={password} onChange={(event) => setPassword(event.target.value)} /></label>{status.bootstrap_needed && <label>Bootstrap secret<input type="password" value={bootstrapSecret} onChange={(event) => setBootstrapSecret(event.target.value)} /></label>}<button onClick={submit} disabled={!username || !password || (status.bootstrap_needed && !bootstrapSecret)}>{status.bootstrap_needed ? 'Create and sign in' : 'Sign in'}</button></div></div>;
  return <><App />{status.required && <div className="auth-session"><span>{identity}</span><button className="quiet" onClick={logout}>Sign out</button></div>}</>;
}
