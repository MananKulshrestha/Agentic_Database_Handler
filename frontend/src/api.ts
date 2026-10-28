export type Connection = { id: string; name: string; engine: string; host: string; port: number; database: string; auth_source: string; username: string; schema_scope: string[]; capabilities: Record<string, unknown> };
export type Snapshot = { id: string; connection_id: string; created_at: string; schema_hash: string; coverage: { warnings?: string[]; object_count?: number }; status: string };
export type SchemaObject = { id: string; snapshot_id: string; kind: string; schema_name: string; name: string; definition: { columns?: Array<{ name: string; type: string; nullable?: boolean }>; primary_key?: { constrained_columns?: string[] }; foreign_keys?: Array<{ constrained_columns?: string[]; referred_table?: string }>; indexes?: Array<{ name: string; column_names: string[] }>; comment?: { text?: string } } };
export type Finding = { id: string; rule_id: string; severity: string; category: string; observation: string; recommendation: string; evidence: Record<string, unknown>; assumptions: string[]; certainty: string; status: string; object_ids: string[]; note: string };
export type Artifact = { id: string; kind: string; title: string; markdown: string; content: Record<string, unknown>; created_at: string };
export type ArtifactSummary = Pick<Artifact, 'id' | 'kind' | 'title' | 'created_at'>;
export type Run = { id: string; kind: string; status: string; stage: string; result: Record<string, unknown>; error: string; created_at: string };
export type Conversation = { id: string; connection_id: string; title: string };
export type Message = { id: string; role: string; content: string; evidence: Array<{kind: string; id: string}> };
export type BusinessRule = { id: string; object_name: string; description: string; dependency: Record<string, unknown>; status: string };

export function getAuthToken(): string {
  return typeof sessionStorage === 'undefined' ? '' : sessionStorage.getItem('dbh_access_token') || '';
}

export function setAuthToken(token: string) {
  if (typeof sessionStorage !== 'undefined') sessionStorage.setItem('dbh_access_token', token);
}

export function clearAuthToken() {
  if (typeof sessionStorage !== 'undefined') sessionStorage.removeItem('dbh_access_token');
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const token = getAuthToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.message === 'string' ? body.message : typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const post = <T>(path: string, body: unknown) => api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });

export async function downloadArtifact(artifactId: string, format: 'markdown' | 'json' = 'markdown') {
  const token = getAuthToken();
  const response = await fetch(`/api/v1/artifacts/${artifactId}/export?format=${format}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new Error(`Export failed (${response.status})`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = `artifact-${artifactId}.${format === 'markdown' ? 'md' : 'json'}`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function observeRun(runId: string, onEvent: (event: string, data: Record<string, unknown>) => void): () => void {
  // Native EventSource cannot send the bearer header; authenticated mode uses the status poller.
  if (getAuthToken()) return () => {};
  const source = new EventSource(`/api/v1/runs/${runId}/events`);
  const names = ['run.started', 'stage.changed', 'tool.completed', 'assistant.delta', 'artifact.created', 'run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'];
  for (const name of names) source.addEventListener(name, (event) => {
    const data = JSON.parse((event as MessageEvent).data) as Record<string, unknown>;
    onEvent(name, data);
    if (['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted'].includes(name)) source.close();
  });
  source.onerror = () => source.close();
  return () => source.close();
}
