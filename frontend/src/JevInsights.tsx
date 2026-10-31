import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type Artifact, type Run } from './api';

type Decision = { value: string | number; confidence: number };
type Insight = { id: string; observation?: string; model?: string; decisions?: Record<string, Decision>; error?: string };
type Batch = { items: Insight[]; has_more?: boolean; changes?: Array<{ id: string; schema: string; name: string; change: string; impact?: string }> };

function label(value?: string | number) {
  return String(value ?? '—').replaceAll('_', ' ');
}

function resultError(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function DecisionLine({ name, decision }: { name: string; decision?: Decision }) {
  if (!decision) return null;
  return <span className="muted">{name}: <strong>{label(decision.value)}</strong> ({Math.round(decision.confidence * 100)}% confidence{decision.confidence < 0.7 ? '; uncertain' : ''})</span>;
}

export function JevFindingPanel({ snapshotId, enabled }: { snapshotId: string; enabled: boolean }) {
  const [items, setItems] = useState<Insight[]>([]);
  const [nextCursor, setNextCursor] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function assess(cursor: number) {
    setBusy(true); setError('');
    try {
      const page = await api<Batch>(`/jev/findings?snapshot_id=${snapshotId}&cursor=${cursor}`);
      setItems((previous) => [...(cursor === 0 ? [] : previous), ...page.items].sort((a, b) => Number(b.decisions?.priority.value ?? -1) - Number(a.decisions?.priority.value ?? -1)));
      setNextCursor(cursor + page.items.length);
      setHasMore(Boolean(page.has_more));
    } catch (exc) { setError(resultError(exc)); }
    finally { setBusy(false); }
  }

  return <div className="card"><h2>Jev investigation order</h2><p>Advisory ratings based on saved finding evidence. Review each recommendation before acting.</p>
    {!enabled && <p className="muted">Set TYPESAFE_API_KEY and TYPESAFE_ALLOW_SCHEMA_SHARING=true on the backend to enable Jev.</p>}
    <button className="secondary" disabled={!enabled || busy} onClick={() => assess(0)}>{busy ? 'Assessing…' : items.length ? 'Refresh Jev assessment' : 'Prioritize findings with Jev'}</button>
    {error && <p className="alert error">{error}</p>}
    {items.map((item) => <div className="list-row" key={item.id}><div><strong>{item.observation || item.id}</strong><div className="actions"><DecisionLine name="Priority (0–3)" decision={item.decisions?.priority} /><DecisionLine name="Queue" decision={item.decisions?.triage} /><DecisionLine name="Relevance" decision={item.decisions?.relevance} /><DecisionLine name="Needs" decision={item.decisions?.missing_evidence} /></div>{item.error && <span className="muted">{item.error}</span>}</div></div>)}
    {hasMore && <button className="secondary" disabled={busy} onClick={() => assess(nextCursor)}>Assess next 50 findings</button>}
  </div>;
}

export function JevDiffPanel({ snapshotId, against, enabled }: { snapshotId: string; against: string; enabled: boolean }) {
  const [items, setItems] = useState<Insight[]>([]);
  const [nextCursor, setNextCursor] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [assessed, setAssessed] = useState(false);

  async function assess(cursor: number) {
    setBusy(true); setError('');
    try {
      const page = await api<Batch>(`/jev/snapshots/${snapshotId}/diff?against=${against}&cursor=${cursor}`);
      const byId = new Map(page.changes?.map((change) => [change.id, change]) || []);
      const enriched = page.items.map((item) => { const change = byId.get(item.id); return { ...item, observation: change ? `${change.change}: ${change.schema}.${change.name}` : item.id }; });
      setItems((previous) => [...(cursor === 0 ? [] : previous), ...enriched].sort((a, b) => Number(b.decisions?.priority.value ?? -1) - Number(a.decisions?.priority.value ?? -1)));
      setNextCursor(cursor + page.items.length); setHasMore(Boolean(page.has_more)); setAssessed(true);
    } catch (exc) { setError(resultError(exc)); }
    finally { setBusy(false); }
  }

  return <div><button className="secondary" disabled={!enabled || !against || busy} onClick={() => assess(0)}>{busy ? 'Assessing…' : 'Prioritize changes with Jev'}</button>
    {error && <p className="alert error">{error}</p>}
    {assessed && items.length === 0 && <p className="muted">No schema changes to prioritize.</p>}
    {items.map((item) => <div className="list-row" key={item.id}><span>{item.observation}</span><DecisionLine name="Priority (0–3)" decision={item.decisions?.priority} /><DecisionLine name="Queue" decision={item.decisions?.triage} />{item.error && <span className="muted">{item.error}</span>}</div>)}
    {hasMore && <button className="secondary" disabled={busy} onClick={() => assess(nextCursor)}>Assess next 50 changes</button>}
  </div>;
}

export function JevProposalPanel({ artifact, enabled }: { artifact: Artifact; enabled: boolean }) {
  const supported = ['redesign', 'migration_proposal', 'relationship_suggestions'].includes(artifact.kind);
  const result = useQuery({ queryKey: ['jev-proposal', artifact.id], queryFn: () => api<{ decisions: Record<string, Decision> }>(`/jev/artifacts/${artifact.id}/readiness`), enabled: false, retry: false });
  if (!supported) return null;
  return <section className="card"><h3>Jev review readiness</h3><p className="muted">Checks whether this draft contains enough context for a human review.</p><button className="secondary" disabled={!enabled || result.isFetching} onClick={() => result.refetch()}>{result.isFetching ? 'Assessing…' : 'Assess proposal'}</button>{result.error && <p className="alert error">{resultError(result.error)}</p>}<div><DecisionLine name="Readiness" decision={result.data?.decisions.readiness} /></div></section>;
}

export function JevActivityPanel({ runs, enabled }: { runs: Run[]; enabled: boolean }) {
  const result = useQuery({ queryKey: ['jev-runs', runs.map((run) => `${run.id}:${run.status}`).join(',')], queryFn: () => api<Batch>('/jev/runs?limit=12'), enabled: false, retry: false });
  const byId = new Map(runs.map((run) => [run.id, run]));
  return <div className="card"><h2>Jev activity attention</h2><p className="muted">Highlights runs that may need a closer look.</p><button className="secondary" disabled={!enabled || result.isFetching || runs.length === 0} onClick={() => result.refetch()}>{result.isFetching ? 'Assessing…' : 'Prioritize activity with Jev'}</button>{result.error && <p className="alert error">{resultError(result.error)}</p>}{result.data?.items.map((item) => <div className="list-row" key={item.id}><span>{byId.get(item.id)?.kind.replaceAll('_', ' ') || item.id} · {byId.get(item.id)?.status}</span><DecisionLine name="Attention (0–3)" decision={item.decisions?.attention} /><DecisionLine name="Queue" decision={item.decisions?.triage} />{item.error && <span className="muted">{item.error}</span>}</div>)}</div>;
}
