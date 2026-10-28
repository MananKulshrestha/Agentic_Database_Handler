import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import dagre from '@dagrejs/dagre';
import { ReactFlow, Background, Controls, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { api, type SchemaObject } from './api';

export default function Diagram({ snapshotId, onSelect }: { snapshotId: string; onSelect: (id: string) => void }) {
  const graph = useQuery({ queryKey: ['graph', snapshotId], queryFn: () => api<{ nodes: SchemaObject[]; edges: Array<{ id: string; source_object_id: string; target_object_id: string }> }>(`/snapshots/${snapshotId}/graph`), enabled: !!snapshotId });
  const laidOut = useMemo(() => {
    if (!graph.data) return { nodes: [] as Node[], edges: [] as Edge[] };
    const g = new dagre.graphlib.Graph();
    g.setGraph({ rankdir: 'LR', nodesep: 45, ranksep: 100 });
    g.setDefaultEdgeLabel(() => ({}));
    for (const item of graph.data.nodes) g.setNode(item.id, { width: 170, height: 56 });
    for (const item of graph.data.edges) g.setEdge(item.source_object_id, item.target_object_id);
    dagre.layout(g);
    return {
      nodes: graph.data.nodes.map((item) => ({ id: item.id, data: { label: `${item.schema_name}.${item.name}` }, position: { x: g.node(item.id).x - 85, y: g.node(item.id).y - 28 }, style: { width: 170, border: '1px solid #99b0c7', borderRadius: 12, background: '#ffffff', color: '#17263b', fontSize: 12, padding: 10 } })),
      edges: graph.data.edges.map((item) => ({ id: item.id, source: item.source_object_id, target: item.target_object_id, animated: false, style: { stroke: '#59779b' } })),
    };
  }, [graph.data]);
  if (graph.isLoading) return <p className="muted">Loading relationships…</p>;
  if (!laidOut.nodes.length) return <p className="muted">No schema objects to draw.</p>;
  return <div className="diagram"><ReactFlow nodes={laidOut.nodes} edges={laidOut.edges} fitView onNodeClick={(_, node) => onSelect(node.id)}><Background gap={20} color="#e8edf3" /><Controls /></ReactFlow></div>;
}
