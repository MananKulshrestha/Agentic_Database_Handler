import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, getAuthToken, patch, post, type Connection, type SchemaObject, type Snapshot } from './api';

type Document = { id: string; filename: string; kind: string };
type Sandbox = { id: string; name: string; connection_id: string };
type Schedule = { id: string; name: string; connection_id: string; interval_minutes: number; enabled: boolean; next_run_at: string };
type ExperimentRecord = { id: string; artifact_id: string; created_at: string; summary: { before_ms?: number; after_ms?: number; synthetic_rows?: number } };

export default function AdvancedTools({ connectionId, snapshotId, snapshots, selectedObject, disabled, start, onError }: {
  connectionId: string;
  snapshotId: string;
  snapshots: Snapshot[];
  selectedObject?: SchemaObject;
  disabled: boolean;
  start: (kind: string, extra?: Record<string, unknown>) => Promise<void>;
  onError: (message: string) => void;
}) {
  const qc = useQueryClient();
  const [documentId, setDocumentId] = useState('');
  const [beforeId, setBeforeId] = useState('');
  const [column, setColumn] = useState('');
  const [rowCount, setRowCount] = useState(3000);
  const [targetEngine, setTargetEngine] = useState('postgresql');
  const [dialect, setDialect] = useState('postgres');
  const [sandboxId, setSandboxId] = useState('');
  const [sandboxConnectionId, setSandboxConnectionId] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [scanInterval, setScanInterval] = useState(60);
  const documents = useQuery({ queryKey: ['context-documents', connectionId], queryFn: () => api<Document[]>(`/context-documents?connection_id=${connectionId}`), enabled: !!connectionId });
  const connections = useQuery({ queryKey: ['connections'], queryFn: () => api<Connection[]>('/connections') });
  const sandboxes = useQuery({ queryKey: ['sandboxes'], queryFn: () => api<Sandbox[]>('/sandboxes') });
  const schedules = useQuery({ queryKey: ['schedules'], queryFn: () => api<Schedule[]>('/schedules') });
  const experiments = useQuery({ queryKey: ['experiments', sandboxId], queryFn: () => api<ExperimentRecord[]>(`/sandboxes/${sandboxId}/experiments`), enabled: !!sandboxId });

  async function createSandbox() {
    try {
      const item = await post<Sandbox>('/sandboxes', { name: 'Temporary PostgreSQL experiment', connection_id: sandboxConnectionId, acknowledge_temporary_writes: acknowledged });
      setSandboxId(item.id);
      qc.invalidateQueries({ queryKey: ['sandboxes'] });
    } catch (error) { onError(String(error)); }
  }

  async function createSchedule() {
    try {
      await post('/schedules', { name: 'Scheduled schema scan', connection_id: connectionId, interval_minutes: scanInterval, enabled: true });
      qc.invalidateQueries({ queryKey: ['schedules'] });
    } catch (error) { onError(String(error)); }
  }

  async function toggleSchedule(item: Schedule) {
    try {
      await patch(`/schedules/${item.id}`, { enabled: !item.enabled });
      qc.invalidateQueries({ queryKey: ['schedules'] });
    } catch (error) { onError(String(error)); }
  }

  async function upload(file?: File) {
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    try {
      const token = getAuthToken();
      const response = await fetch(`/api/v1/context-documents?connection_id=${connectionId}`, { method: 'POST', body, headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!response.ok) throw new Error(`Upload failed (${response.status})`);
      const result = await response.json() as Document;
      setDocumentId(result.id);
      qc.invalidateQueries({ queryKey: ['context-documents', connectionId] });
    } catch (error) { onError(String(error)); }
  }

  return <>
    <div className="card"><h2>More analysis</h2><p>These tools produce reviewable reports or run bounded experiments. They do not change the connected database.</p>
      <div className="actions">
        <button className="secondary" disabled={disabled} onClick={() => start('infer_relationships')}>Suggest relationships</button>
        <button className="secondary" disabled={disabled} onClick={() => start('workload_analysis', documentId ? { document_id: documentId } : {})}>{documentId ? 'Analyze uploaded SQL workload' : 'Inspect live workload'}</button>
        <button className="secondary" disabled={!selectedObject || disabled} onClick={() => start('generate_demo_data', { object_id: selectedObject?.id, row_count: 10 })}>Draft sample data</button>
      </div>
    </div>
    <div className="card"><h2>Compare snapshots</h2><p>Choose an earlier scan from the same connection.</p><div className="actions"><select style={{ maxWidth: 340 }} value={beforeId} onChange={(e) => setBeforeId(e.target.value)}><option value="">Earlier snapshot</option>{snapshots.filter((s) => s.id !== snapshotId).map((s) => <option key={s.id} value={s.id}>{new Date(s.created_at).toLocaleString()}</option>)}</select><button disabled={!beforeId || disabled} onClick={() => start('schema_diff', { snapshot_id: undefined, before_snapshot_id: beforeId, after_snapshot_id: snapshotId })}>Compare</button></div></div>
    <div className="card"><h2>Index experiment</h2><p>Compare a query before and after adding an index to synthetic data. Without a sandbox, this uses in-memory SQLite. A selected PostgreSQL sandbox uses a temporary table that is removed when the experiment ends; neither mode changes the inspected schema.</p><div className="field-grid"><label>Column<select value={column} onChange={(e) => setColumn(e.target.value)}><option value="">Choose a column</option>{selectedObject?.definition.columns?.map((c) => <option key={c.name}>{c.name}</option>)}</select></label><label>Synthetic rows<input type="number" min="100" max="20000" value={rowCount} onChange={(e) => setRowCount(Number(e.target.value))} /></label></div><label>Experiment engine<select value={sandboxId} onChange={(e) => setSandboxId(e.target.value)}><option value="">SQLite simulation</option>{sandboxes.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><button disabled={!selectedObject || !column || disabled} onClick={() => start('experiment', { object_id: selectedObject?.id, column, row_count: rowCount, sandbox_id: sandboxId || undefined })}>Run experiment</button>{experiments.data?.map((item) => <div className="list-row" key={item.id}><span>{new Date(item.created_at).toLocaleString()}</span><span>{item.summary.synthetic_rows} rows</span><span>{item.summary.before_ms} → {item.summary.after_ms} ms</span></div>)}<div className="field-grid"><label>Register a PostgreSQL sandbox connection<select value={sandboxConnectionId} onChange={(e) => setSandboxConnectionId(e.target.value)}><option value="">Choose a connection</option>{connections.data?.filter((item) => item.engine === 'postgresql').map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label><input type="checkbox" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} /> I allow temporary synthetic writes on this connection</label></div><button className="secondary" disabled={!sandboxConnectionId || !acknowledged} onClick={createSandbox}>Register sandbox</button></div>
    <div className="card"><h2>Source context and offline DDL</h2><p>Upload a small SQL, Markdown, JSON, Python, or TypeScript text file. SQL CREATE TABLE statements can be imported as a partial snapshot.</p><input type="file" accept=".sql,.md,.txt,.json,.py,.ts" onChange={(e) => upload(e.target.files?.[0])} /><div className="actions"><select style={{ maxWidth: 340 }} value={documentId} onChange={(e) => setDocumentId(e.target.value)}><option value="">Choose uploaded file</option>{documents.data?.map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}</select><button className="secondary" disabled={!documentId || disabled} onClick={() => start('analyze_context', { document_ids: [documentId] })}>Find object references</button></div><div className="actions"><select style={{ maxWidth: 180 }} value={dialect} onChange={(e) => setDialect(e.target.value)}><option value="postgres">PostgreSQL</option><option value="mysql">MariaDB</option></select><button className="secondary" disabled={!documentId || disabled} onClick={() => start('import_schema', { snapshot_id: undefined, document_id: documentId, dialect })}>Import SQL schema</button></div></div>
    <div className="card"><h2>Migration inventory</h2><p>Collects the objects and compatibility work to review before moving to another engine.</p><div className="actions"><select style={{ maxWidth: 180 }} value={targetEngine} onChange={(e) => setTargetEngine(e.target.value)}><option value="postgresql">PostgreSQL</option><option value="mariadb">MariaDB</option><option value="mongodb">MongoDB</option></select><button disabled={disabled} onClick={() => start('migration_proposal', { target_engine: targetEngine })}>Create inventory</button></div></div>
    <div className="card"><h2>Scheduled scans</h2><p>This local single-instance scheduler saves its schedule and submits read-only schema scans while the backend is running.</p><div className="actions"><label>Interval (minutes)<input type="number" min="5" max="10080" value={scanInterval} onChange={(e) => setScanInterval(Number(e.target.value))} /></label><button disabled={!connectionId || scanInterval < 5 || scanInterval > 10080} onClick={createSchedule}>Add schedule</button></div>{schedules.data?.filter((item) => item.connection_id === connectionId).map((item) => <div className="list-row" key={item.id}><span>{item.name} · every {item.interval_minutes} minutes</span><span className="muted">Next: {new Date(item.next_run_at).toLocaleString()}</span><button className="quiet" onClick={() => toggleSchedule(item)}>{item.enabled ? 'Pause' : 'Resume'}</button></div>)}</div>
  </>;
}
