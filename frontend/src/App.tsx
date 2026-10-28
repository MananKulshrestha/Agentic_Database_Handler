import { lazy, Suspense, useEffect, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Activity, BookOpen, Bot, Database, FileText, GitBranch, MessageSquare, Play, Plus, Search, ShieldCheck, Table2 } from 'lucide-react';
import { api, downloadArtifact, observeRun, patch, post, type Artifact, type ArtifactSummary, type BusinessRule, type Connection, type Conversation, type Finding, type Message, type Run, type SchemaObject, type Snapshot } from './api';
import AdvancedTools from './AdvancedTools';
import ApprovalControls from './ApprovalControls';
import ProjectMembers from './ProjectMembers';

type View = 'overview' | 'explorer' | 'findings' | 'documentation' | 'design' | 'query';
type RunStart = { run_id: string; events_url: string };
type AuditEvent = { id: string; action: string; resource_type: string; resource_id: string; actor: string; created_at: string };
const Diagram = lazy(() => import('./Diagram'));

function Markdown({ children }: { children: string }) {
  return <div className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown></div>;
}

export default function App() {
  const qc = useQueryClient();
  const [connectionId, setConnectionId] = useState('');
  const [snapshotId, setSnapshotId] = useState('');
  const [objectId, setObjectId] = useState('');
  const [view, setView] = useState<View>('overview');
  const [showConnectionForm, setShowConnectionForm] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [activeRun, setActiveRun] = useState('');
  const [activeArtifactId, setActiveArtifactId] = useState('');
  const [chatText, setChatText] = useState('');
  const [chatDelta, setChatDelta] = useState('');
  const [conversationId, setConversationId] = useState('');
  const [sql, setSql] = useState('');
  const [queryQuestion, setQueryQuestion] = useState('Show rows from the selected table');
  const [workload, setWorkload] = useState('');
  const [normalLevel, setNormalLevel] = useState('3NF');
  const [dependencyLeft, setDependencyLeft] = useState('');
  const [dependencyRight, setDependencyRight] = useState('');
  const [ruleDescription, setRuleDescription] = useState('');
  const [objectSearch, setObjectSearch] = useState('');
  const [connectionForm, setConnectionForm] = useState({ name: 'Demo PostgreSQL', engine: 'postgresql', host: '127.0.0.1', port: 5433, database: 'demo', auth_source: 'admin', username: 'demo_read', password: 'demo_read', schema_scope: ['public'] });

  const connections = useQuery({ queryKey: ['connections'], queryFn: () => api<Connection[]>('/connections') });
  useEffect(() => { if (!connectionId && connections.data?.length) setConnectionId(connections.data[0].id); }, [connections.data, connectionId]);
  const snapshots = useQuery({ queryKey: ['snapshots', connectionId], queryFn: () => api<Snapshot[]>(`/snapshots?connection_id=${connectionId}`), enabled: !!connectionId });
  useEffect(() => { if (snapshots.data?.length && !snapshots.data.some((s) => s.id === snapshotId)) setSnapshotId(snapshots.data[0].id); }, [snapshots.data, snapshotId]);
  useEffect(() => { setObjectId(''); setActiveArtifactId(''); }, [snapshotId]);
  const objectPages = useInfiniteQuery({ queryKey: ['objects', snapshotId], queryFn: ({ pageParam }) => api<SchemaObject[]>(`/snapshots/${snapshotId}/objects?limit=200&cursor=${pageParam}`), initialPageParam: 0, getNextPageParam: (page, pages) => page.length === 200 ? pages.length * 200 : undefined, enabled: !!snapshotId });
  const findingPages = useInfiniteQuery({ queryKey: ['findings', snapshotId], queryFn: ({ pageParam }) => api<Finding[]>(`/findings?snapshot_id=${snapshotId}&limit=200&cursor=${pageParam}`), initialPageParam: 0, getNextPageParam: (page, pages) => page.length === 200 ? pages.length * 200 : undefined, enabled: !!snapshotId });
  const objects = { data: objectPages.data?.pages.flat() };
  const findings = { data: findingPages.data?.pages.flat() };
  const artifacts = useQuery({ queryKey: ['artifacts', snapshotId], queryFn: () => api<ArtifactSummary[]>(`/artifacts?snapshot_id=${snapshotId}`), enabled: !!snapshotId });
  const activeArtifact = useQuery({ queryKey: ['artifact', activeArtifactId], queryFn: () => api<Artifact>(`/artifacts/${activeArtifactId}`), enabled: !!activeArtifactId });
  const selectedObject = objects.data?.find((item) => item.id === objectId);
  const conversations = useQuery({ queryKey: ['conversations', connectionId], queryFn: () => api<Conversation[]>(`/conversations?connection_id=${connectionId}`), enabled: !!connectionId });
  const messages = useQuery({ queryKey: ['messages', conversationId], queryFn: () => api<{ messages: Message[] }>(`/conversations/${conversationId}`), enabled: !!conversationId });
  const businessRules = useQuery({ queryKey: ['business-rules', connectionId], queryFn: () => api<BusinessRule[]>(`/business-rules?connection_id=${connectionId}`), enabled: !!connectionId });
  const recentRuns = useQuery({ queryKey: ['runs'], queryFn: () => api<Run[]>('/runs?limit=12') });
  const auditEvents = useQuery({ queryKey: ['audit-events'], queryFn: () => api<AuditEvent[]>('/audit-events?limit=12') });

  function refresh() {
    qc.invalidateQueries({ queryKey: ['snapshots'] });
    qc.invalidateQueries({ queryKey: ['objects'] });
    qc.invalidateQueries({ queryKey: ['graph'] });
    qc.invalidateQueries({ queryKey: ['findings'] });
    qc.invalidateQueries({ queryKey: ['artifacts'] });
    qc.invalidateQueries({ queryKey: ['messages'] });
    qc.invalidateQueries({ queryKey: ['runs'] });
    qc.invalidateQueries({ queryKey: ['audit-events'] });
    qc.invalidateQueries({ queryKey: ['experiments'] });
  }

  function watch(runId: string) {
    setActiveRun(runId);
    const stop = observeRun(runId, (name, data) => {
      if (name === 'stage.changed') setNotice(String(data.stage || 'Working…'));
      if (name === 'assistant.delta') setChatDelta((old) => old + String(data.text || ''));
      if (name === 'run.completed') {
        setActiveRun('');
        setNotice('Finished.');
        setChatDelta('');
        if (data.artifact_id) setActiveArtifactId(String(data.artifact_id));
        if (data.sql) setSql(String(data.sql));
        if (data.snapshot_id) setSnapshotId(String(data.snapshot_id));
        refresh();
      }
      if (name === 'run.failed') { setActiveRun(''); setNotice(''); setError(String(data.error || 'The operation failed.')); }
    });
    // Polling covers a temporary event-stream interruption.
    const poll = window.setInterval(async () => {
      try {
        const run = await api<Run>(`/runs/${runId}`);
        if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(run.status)) {
          window.clearInterval(poll);
          stop();
          setActiveRun('');
          if (run.status === 'succeeded') {
            setNotice('Finished.');
            if (run.result.artifact_id) setActiveArtifactId(String(run.result.artifact_id));
            if (run.result.sql) setSql(String(run.result.sql));
            if (run.result.snapshot_id) setSnapshotId(String(run.result.snapshot_id));
            setChatDelta('');
            refresh();
          } else { setNotice(''); setError(run.error || `Run ${run.status}`); }
        }
      } catch { /* Keep the stream as the primary status source. */ }
    }, 2000);
  }

  async function start(kind: string, extra: Record<string, unknown> = {}) {
    setError(''); setActiveArtifactId(''); setNotice(`Starting ${kind.replaceAll('_', ' ')}…`);
    try {
      const result = await post<RunStart>('/runs', { kind, connection_id: connectionId, snapshot_id: kind === 'scan' ? undefined : snapshotId, ...extra });
      watch(result.run_id);
    } catch (exc) { setError(String(exc)); setNotice(''); }
  }

  async function sendChat() {
    if (!connectionId) { setError('Choose a connection first.'); return; }
    if (!chatText.trim() || !snapshotId) return;
    setError(''); setChatDelta('');
    try {
      let id = conversationId;
      if (!id) {
        const conversation = await post<Conversation>('/conversations', { connection_id: connectionId, title: chatText.slice(0, 80) });
        id = conversation.id; setConversationId(id);
        qc.invalidateQueries({ queryKey: ['conversations', connectionId] });
      }
      const response = await post<RunStart>(`/conversations/${id}/messages`, { text: chatText, snapshot_id: snapshotId, object_ids: objectId ? [objectId] : [] });
      setChatText(''); qc.invalidateQueries({ queryKey: ['messages', id] }); watch(response.run_id);
    } catch (exc) { setError(String(exc)); }
  }

  async function saveConnection() {
    setError('');
    try {
      const created = await post<Connection>('/connections', connectionForm);
      await qc.invalidateQueries({ queryKey: ['connections'] });
      setConnectionId(created.id); setSnapshotId(''); setShowConnectionForm(false); setNotice('Connection saved. Test it, then scan.');
    } catch (exc) { setError(String(exc)); }
  }

  async function testConnection() {
    try {
      const result = await post<Record<string, unknown>>(`/connections/${connectionId}/test`, {});
      setNotice(`Connected. ${String(result.engine_version || '')}`);
    } catch (exc) { setError(String(exc)); }
  }

  async function updateFinding(id: string, status: string) {
    try { await patch(`/findings/${id}`, { status, note: '' }); qc.invalidateQueries({ queryKey: ['findings', snapshotId] }); }
    catch (exc) { setError(String(exc)); }
  }

  async function addRule() {
    if (!ruleDescription.trim()) return;
    const left = dependencyLeft.split(',').map((s) => s.trim()).filter(Boolean);
    const right = dependencyRight.split(',').map((s) => s.trim()).filter(Boolean);
    try {
      await post('/business-rules', { connection_id: connectionId, object_name: selectedObject ? `${selectedObject.schema_name}.${selectedObject.name}` : '', description: ruleDescription, dependency: left.length && right.length ? { determinant: left, dependent: right } : {}, status: 'confirmed' });
      setRuleDescription(''); setDependencyLeft(''); setDependencyRight('');
      qc.invalidateQueries({ queryKey: ['business-rules', connectionId] });
    } catch (exc) { setError(String(exc)); }
  }

  const nav: Array<{ key: View; label: string; icon: typeof Database }> = [
    { key: 'overview', label: 'Overview', icon: Activity }, { key: 'explorer', label: 'Schema explorer', icon: Table2 },
    { key: 'findings', label: 'Findings', icon: ShieldCheck }, { key: 'documentation', label: 'Documentation', icon: BookOpen },
    { key: 'design', label: 'Design lab', icon: GitBranch }, { key: 'query', label: 'Query lab', icon: FileText },
  ];

  return <div className="app-shell">
    <aside className="left-sidebar">
      <div className="brand"><Database size={22} /><div><strong>Database Workbench</strong><small>Understand your data</small></div></div>
      <label className="field-label">Connection</label>
      <select value={connectionId} onChange={(event) => { setConnectionId(event.target.value); setSnapshotId(''); setObjectId(''); setConversationId(''); }}>
        <option value="">Choose a connection</option>{connections.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
      <button className="quiet full" onClick={() => setShowConnectionForm(!showConnectionForm)}><Plus size={16} /> Add connection</button>
      {showConnectionForm && <div className="connection-form">
        {(['name', 'host', 'database', 'username', 'password'] as const).map((key) => <label key={key}>{key}<input type={key === 'password' ? 'password' : 'text'} value={connectionForm[key]} onChange={(e) => setConnectionForm({ ...connectionForm, [key]: e.target.value })} /></label>)}
        <label>Engine<select value={connectionForm.engine} onChange={(e) => setConnectionForm({ ...connectionForm, engine: e.target.value, port: e.target.value === 'mongodb' ? 27017 : e.target.value === 'mariadb' ? 3306 : 5432, schema_scope: e.target.value === 'postgresql' ? ['public'] : [] })}><option value="postgresql">PostgreSQL</option><option value="mariadb">MariaDB</option><option value="mongodb">MongoDB</option></select></label>
        {connectionForm.engine === 'mongodb' && <label>Authentication database<input value={connectionForm.auth_source} onChange={(e) => setConnectionForm({ ...connectionForm, auth_source: e.target.value })} /></label>}
        <label>Port<input type="number" value={connectionForm.port} onChange={(e) => setConnectionForm({ ...connectionForm, port: Number(e.target.value) })} /></label>
        <label>Schema scope<input value={connectionForm.schema_scope.join(', ')} onChange={(e) => setConnectionForm({ ...connectionForm, schema_scope: e.target.value.split(',').map((x) => x.trim()).filter(Boolean) })} /></label>
        <button onClick={saveConnection}>Save connection</button>
      </div>}
      {connectionId && <div className="connection-actions"><button className="quiet" onClick={testConnection}>Test</button><button className="quiet" disabled={!!activeRun} onClick={() => start('scan', { snapshot_id: undefined })}><Play size={14} /> Scan</button></div>}
      <label className="field-label">Snapshot</label>
      <select value={snapshotId} onChange={(e) => { setSnapshotId(e.target.value); setObjectId(''); }} disabled={!snapshots.data?.length}>
        <option value="">No snapshot</option>{snapshots.data?.map((item) => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString()} · {item.status}</option>)}
      </select>
      <nav>{nav.map(({ key, label, icon: Icon }) => <button key={key} className={view === key ? 'nav active' : 'nav'} onClick={() => setView(key)}><Icon size={17} /> {label}</button>)}</nav>
      <div className="sidebar-footer">Local project workspace<br />Saved snapshots keep exploration quick.</div>
    </aside>

    <main className="main-area">
      <header className="topbar"><div><span className="eyebrow">WORKSPACE</span><h1>{nav.find((n) => n.key === view)?.label}</h1></div><div className="actions">{activeRun && <button className="quiet" onClick={() => post(`/runs/${activeRun}/cancel`, {}).catch((exc) => setError(String(exc)))}>Cancel task</button>}<span className="top-meta">{connections.data?.find((x) => x.id === connectionId)?.engine || 'No database selected'}</span></div></header>
      {error && <div className="alert error"><span>{error}</span><button onClick={() => setError('')}>Dismiss</button></div>}
      {notice && <div className="alert notice"><span>{notice}</span><button onClick={() => setNotice('')}>Dismiss</button></div>}
      {!connectionId && <div className="empty"><Database size={40} /><h2>Connect a database</h2><p>Add a connection on the left. The supplied demo database is ready for a first scan after you start the local services.</p></div>}
      {connectionId && !snapshotId && <div className="empty"><Search size={40} /><h2>Start with a schema scan</h2><p>Test the connection, then scan it to create a snapshot you can explore and discuss.</p><button disabled={!!activeRun} onClick={() => start('scan', { snapshot_id: undefined })}>Scan database</button></div>}
      {snapshotId && view === 'overview' && <div className="page-content">
        <div className="stats"><div><strong>{objects.data?.length ?? '—'}</strong><span>Objects</span></div><div><strong>{findings.data?.length ?? '—'}</strong><span>Findings</span></div><div><strong>{snapshots.data?.length ?? '—'}</strong><span>Snapshots</span></div></div>
        <div className="card"><h2>Explore this snapshot</h2><p>The snapshot records structure as observed during the scan. Reports and chat reference this specific version.</p><div className="actions"><button disabled={!!activeRun} onClick={() => start('analyze')}>Analyze schema</button><button className="secondary" disabled={!!activeRun} onClick={() => start('document')}>Generate documentation</button></div></div>
        <div className="card"><h2>Recent findings</h2>{findings.data?.slice(0, 5).map((f) => <div key={f.id} className="list-row"><span className={`pill ${f.severity}`}>{f.severity}</span><span>{f.observation}</span></div>)}{!findings.data?.length && <p className="muted">Run an analysis to create findings.</p>}</div>
        <div className="card"><h2>Recent activity</h2>{recentRuns.data?.map((run) => <div className="list-row" key={run.id}><span className="muted">{new Date(run.created_at).toLocaleString()}</span><span>{run.kind.replaceAll('_', ' ')}</span><span className={`pill ${run.status}`}>{run.status}</span>{run.error && <span className="muted">{run.error}</span>}</div>)}{!recentRuns.data?.length && <p className="muted">No tasks have run yet.</p>}</div>
        <div className="card"><h2>Audit trail</h2>{auditEvents.data?.map((event) => <div className="list-row" key={event.id}><span className="muted">{new Date(event.created_at).toLocaleString()}</span><span>{event.action}</span><span className="muted">{event.resource_type} {event.resource_id.slice(0, 8)} · {event.actor.slice(0, 8)}</span></div>)}{!auditEvents.data?.length && <p className="muted">No recorded changes yet.</p>}</div>
        <ProjectMembers />
      </div>}
      {snapshotId && view === 'explorer' && <div className="page-content">
        <div className="explorer-grid"><div className="card object-list"><h2>Objects</h2><input placeholder="Filter loaded objects" value={objectSearch} onChange={(e) => setObjectSearch(e.target.value)} />{objects.data?.filter((o) => `${o.schema_name}.${o.name}`.toLowerCase().includes(objectSearch.toLowerCase())).map((obj) => <button key={obj.id} className={objectId === obj.id ? 'object-item selected' : 'object-item'} onClick={() => setObjectId(obj.id)}><Table2 size={15} /><span>{obj.schema_name}.{obj.name}</span><small>{obj.kind}</small></button>)}{objectPages.hasNextPage && <button className="secondary" disabled={objectPages.isFetchingNextPage} onClick={() => objectPages.fetchNextPage()}>{objectPages.isFetchingNextPage ? 'Loading…' : 'Load more objects'}</button>}</div>
          <div className="card object-detail"><h2>{selectedObject ? `${selectedObject.schema_name}.${selectedObject.name}` : 'Select an object'}</h2>{selectedObject ? <><p className="muted">{selectedObject.kind} · {selectedObject.definition.comment?.text || 'No database comment'}</p><table><thead><tr><th>Column</th><th>Type</th><th>Nullable</th></tr></thead><tbody>{selectedObject.definition.columns?.map((col) => <tr key={col.name}><td><code>{col.name}</code></td><td>{col.type}</td><td>{col.nullable ? 'Yes' : 'No'}</td></tr>)}</tbody></table><p>Primary key: {selectedObject.definition.primary_key?.constrained_columns?.join(', ') || 'none declared'}</p><h3>Indexes</h3>{selectedObject.definition.indexes?.length ? selectedObject.definition.indexes.map((idx) => <p key={idx.name}><code>{idx.name}</code> — {idx.column_names.join(', ')}</p>) : <p className="muted">No secondary indexes recorded.</p>}<button className="secondary" onClick={() => { setView('design'); }}>Analyze this table</button></> : <p className="muted">Choose a table or view to see its details.</p>}</div></div>
        <div className="card"><h2>Relationships</h2><Suspense fallback={<p className="muted">Loading diagram…</p>}><Diagram snapshotId={snapshotId} onSelect={setObjectId} /></Suspense></div>
      </div>}
      {snapshotId && view === 'findings' && <div className="page-content"><div className="section-heading"><p>Every finding links to recorded schema evidence. Review suggestions in the context of your application.</p><button disabled={!!activeRun} onClick={() => start('analyze')}>Run analysis</button></div>{findings.data?.map((f) => <div className="card finding" key={f.id}><div className="finding-top"><span className={`pill ${f.severity}`}>{f.severity}</span><span className="muted">{f.category} · {f.certainty}</span></div><h3>{f.observation}</h3><p>{f.recommendation}</p>{f.assumptions.length > 0 && <p className="muted">Assumption: {f.assumptions.join(' ')}</p>}<div className="actions"><button className="quiet" onClick={() => updateFinding(f.id, 'accepted')}>Accept</button><button className="quiet" onClick={() => updateFinding(f.id, 'dismissed')}>Dismiss</button><span className="muted">{f.status}</span></div></div>)}{findingPages.hasNextPage && <button className="secondary" disabled={findingPages.isFetchingNextPage} onClick={() => findingPages.fetchNextPage()}>{findingPages.isFetchingNextPage ? 'Loading…' : 'Load more findings'}</button>}{!findings.data?.length && <div className="empty">No findings yet. Run schema analysis to begin.</div>}</div>}
      {snapshotId && view === 'documentation' && <div className="page-content"><div className="section-heading"><p>Documentation is generated from the saved schema and can be exported as Markdown.</p><div className="actions"><button disabled={!!activeRun} onClick={() => start('document', { object_ids: objectId ? [objectId] : [] })}>Generate documentation</button><button className="secondary" onClick={() => downloadArtifact(activeArtifactId).catch((exc) => setError(String(exc)))} disabled={!activeArtifactId}>Download Markdown</button></div></div><div className="artifact-grid"><div className="card artifact-list"><h2>Reports</h2>{artifacts.data?.map((a) => <button key={a.id} className={activeArtifactId === a.id ? 'object-item selected' : 'object-item'} onClick={() => setActiveArtifactId(a.id)}><FileText size={15} /> {a.title} <small>{a.kind}</small></button>)}</div><div className="card report">{activeArtifact.data ? <><Markdown>{activeArtifact.data.markdown}</Markdown><ApprovalControls artifactId={activeArtifactId} kind={activeArtifact.data.kind} /></> : <p className="muted">Select a report or generate documentation.</p>}</div></div></div>}
      {snapshotId && view === 'design' && <div className="page-content"><div className="card"><h2>Normalization assistant</h2><p>Choose a table and confirm functional dependencies before assessing normal forms. A result only applies under the dependencies you supply.</p><select value={objectId} onChange={(e) => setObjectId(e.target.value)}><option value="">Choose a table</option>{objects.data?.filter((o) => o.kind === 'table').map((o) => <option key={o.id} value={o.id}>{o.schema_name}.{o.name}</option>)}</select><div className="field-grid"><label>Business rule<input value={ruleDescription} onChange={(e) => setRuleDescription(e.target.value)} placeholder="Product ID determines catalog name" /></label><label>Determinant columns<input value={dependencyLeft} onChange={(e) => setDependencyLeft(e.target.value)} placeholder="product_id" /></label><label>Dependent columns<input value={dependencyRight} onChange={(e) => setDependencyRight(e.target.value)} placeholder="product_name" /></label></div><div className="actions"><button className="secondary" onClick={addRule}>Save confirmed rule</button><select value={normalLevel} onChange={(e) => setNormalLevel(e.target.value)}><option>1NF</option><option>2NF</option><option>3NF</option><option>BCNF</option><option>4NF</option><option>5NF</option></select><button disabled={!objectId || !!activeRun} onClick={() => start('normalization', { object_id: objectId, requested_level: normalLevel })}>Analyze normalization</button></div>{businessRules.data?.filter((r) => !selectedObject || r.object_name.endsWith(selectedObject.name)).map((r) => <p className="rule" key={r.id}>{r.description} <span className="muted">({r.status})</span></p>)}</div>
        <div className="card"><h2>Database suitability</h2><p>Describe the application's workload and compare broad database options. The result will show missing evidence.</p><textarea value={workload} onChange={(e) => setWorkload(e.target.value)} placeholder="Describe common reads, joins, writes, consistency needs, and document shapes…" /><button disabled={!workload.trim() || !!activeRun} onClick={() => start('suitability', { workload_description: workload })}>Compare options</button></div>
        <div className="card"><h2>Other investigations</h2><div className="actions"><button className="secondary" disabled={!objectId || !!activeRun} onClick={() => start('profile', { object_ids: [objectId] })}>Profile selected table</button><button className="secondary" disabled={!!activeRun} onClick={() => start('redesign')}>Draft redesign ideas</button><button className="secondary" disabled={!!activeRun} onClick={() => start('migration_proposal', { target_engine: 'postgresql' })}>Draft migration inventory</button></div></div>
        <AdvancedTools connectionId={connectionId} snapshotId={snapshotId} snapshots={snapshots.data || []} selectedObject={selectedObject} disabled={!!activeRun} start={start} onError={setError} />
        {activeArtifact.data && <div className="card report"><Markdown>{activeArtifact.data.markdown}</Markdown><ApprovalControls artifactId={activeArtifactId} kind={activeArtifact.data.kind} /></div>}
      </div>}
      {snapshotId && view === 'query' && <div className="page-content"><div className="card"><h2>Query lab</h2><p>Draft a query from the selected table, inspect an estimated plan, or run a bounded read query.</p><input value={queryQuestion} onChange={(e) => setQueryQuestion(e.target.value)} placeholder="What data do you need?" /><div className="actions"><button className="secondary" disabled={!queryQuestion.trim() || !!activeRun} onClick={() => start('query_draft', { question: queryQuestion, object_ids: objectId ? [objectId] : [] })}>Draft SQL</button></div><textarea className="sql-editor" value={sql} onChange={(e) => setSql(e.target.value)} placeholder="SELECT ..." spellCheck={false} /><div className="actions"><button className="secondary" disabled={!sql.trim() || !!activeRun} onClick={() => start('query_plan', { sql })}>Estimated plan</button><button disabled={!sql.trim() || !!activeRun} onClick={() => start('query_execute', { sql })}>Run read query</button></div></div>{activeArtifact.data && ['query_plan', 'query_result', 'query_draft'].includes(activeArtifact.data.kind) && <div className="card report"><Markdown>{activeArtifact.data.markdown}</Markdown>{activeArtifact.data.kind === 'query_result' && <pre>{JSON.stringify(activeArtifact.data.content, null, 2)}</pre>}</div>}</div>}
    </main>

    <aside className="chat-sidebar"><div className="chat-header"><div><Bot size={21} /><strong>Database assistant</strong></div><small>{snapshotId ? 'Using selected snapshot' : 'Choose a snapshot'}</small></div><div className="conversation-picker"><MessageSquare size={16} /><select value={conversationId} onChange={(e) => setConversationId(e.target.value)}><option value="">New conversation</option>{conversations.data?.map((c) => <option value={c.id} key={c.id}>{c.title}</option>)}</select></div><div className="chat-messages">{!messages.data?.messages.length && <div className="chat-intro"><h3>Ask about your database</h3><p>Try “Which tables lack a primary key?”, “What does this table contain?”, or “What should I investigate first?”</p></div>}{messages.data?.messages.map((m) => <div key={m.id} className={`chat-bubble ${m.role}`}><Markdown>{m.content}</Markdown>{m.evidence?.length > 0 && <small>Evidence: {m.evidence.map((e) => <button key={`${e.kind}-${e.id}`} className="quiet" onClick={() => { if (e.kind === 'object') { setObjectId(e.id); setView('explorer'); } else if (e.kind === 'finding') setView('findings'); else if (e.kind === 'snapshot') setSnapshotId(e.id); }}>{e.kind} {e.id.slice(0, 8)}</button>)}</small>}</div>)}{chatDelta && <div className="chat-bubble assistant"><Markdown>{chatDelta}</Markdown></div>}</div><div className="chat-compose">{selectedObject && <span className="context-chip">Context: {selectedObject.name} <button onClick={() => setObjectId('')}>×</button></span>}<textarea value={chatText} onChange={(e) => setChatText(e.target.value)} placeholder="Ask a question about this database…" onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } }} /><button disabled={!snapshotId || !chatText.trim() || !!activeRun} onClick={sendChat}>Send</button></div></aside>
  </div>;
}
