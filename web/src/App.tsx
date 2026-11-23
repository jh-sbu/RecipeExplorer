import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useNodesState,
  useReactFlow,
  type Edge,
  type OnNodeDrag,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { getGraph, getMeta } from "./api";
import { Details } from "./Details";
import { money, qty } from "./format";
import { deltaTone, ItemNode, type FlowNode } from "./ItemNode";
import { layout } from "./layout";
import { loadPlan, savePlan } from "./plan";
import { Sidebar } from "./Sidebar";
import type { Graph, Meta, Override, Plan } from "./types";

const nodeTypes = { item: ItemNode };
type XY = { x: number; y: number };

export function App() {
  const [plan, setPlanState] = useState<Plan>(loadPlan);
  const setPlan = useCallback((fn: (p: Plan) => Plan) => setPlanState(fn), []);
  useEffect(() => savePlan(plan), [plan]);

  const [meta, setMeta] = useState<Meta | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [autoPos, setAutoPos] = useState<Record<string, XY>>({});
  const [layoutNonce, setLayoutNonce] = useState(0);
  const rf = useReactFlow<FlowNode>();

  useEffect(() => {
    getMeta().then(setMeta, (e) => setError(e.message));
  }, []);

  // Re-plan when anything that changes the graph changes (not positions or display mode).
  const query = JSON.stringify([plan.targets.map((t) => [t.item_id, t.quantity]), plan.overrides, plan.settings]);
  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    const t = setTimeout(() => {
      getGraph(plan, ctrl.signal)
        .then((g) => (setGraph(g), setError(null)))
        .catch((e) => e.name !== "AbortError" && setError(e.message))
        .finally(() => !ctrl.signal.aborted && setLoading(false));
    }, 120);
    return () => (clearTimeout(t), ctrl.abort());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  // Lay out again only when the graph's shape changes, not when prices do.
  const shape = graph
    ? `${graph.nodes.map((n) => n.id).sort().join(",")}|${graph.edges.map((e) => e.id).sort().join(",")}`
    : "";
  useEffect(() => {
    if (!graph) return;
    let cancelled = false;
    const measured = new Map(
      rf
        .getNodes()
        .filter((n) => n.measured?.width && n.measured?.height)
        .map((n) => [n.id, { width: n.measured!.width!, height: n.measured!.height! }]),
    );
    layout(graph, measured).then((pos) => {
      if (cancelled) return;
      setAutoPos(pos);
      requestAnimationFrame(() => rf.fitView({ padding: 0.12, duration: 250, maxZoom: 1.1 }));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape, layoutNonce]);

  const onOverride = useCallback(
    (itemId: number, o: Override | null) =>
      setPlan((p) => {
        const overrides = { ...p.overrides };
        if (o) overrides[itemId] = o;
        else delete overrides[itemId];
        return { ...p, overrides };
      }),
    [setPlan],
  );

  const flowNodes = useMemo<FlowNode[]>(() => {
    if (!graph) return [];
    const consumers = new Set(graph.edges.map((e) => e.source));
    return graph.nodes.map((n) => {
      const pos = plan.positions[n.id] ?? autoPos[n.id];
      return {
        id: n.id,
        type: "item",
        position: pos ?? { x: 0, y: 0 },
        // Not `hidden`: hidden nodes get no handle measurements, and edges then attach wrongly.
        style: pos ? undefined : { opacity: 0 },
        selected: n.id === selectedId,
        data: {
          node: n,
          deltaMode: plan.deltaMode,
          override: n.cycle_copy ? undefined : plan.overrides[n.item_id],
          hasConsumers: consumers.has(n.id),
          onOverride,
        },
      };
    });
  }, [graph, autoPos, plan.positions, plan.overrides, plan.deltaMode, selectedId, onOverride]);

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  // Carry `measured` over: React Flow drops a node's handle positions when it's missing, and
  // a node whose size didn't change is never re-measured, so its edges would attach wrongly.
  useEffect(
    () =>
      setNodes((prev) => {
        const measured = new Map(prev.map((n) => [n.id, n.measured]));
        return flowNodes.map((n) => ({ ...n, measured: measured.get(n.id) }));
      }),
    [flowNodes, setNodes],
  );

  const edges = useMemo<Edge[]>(() => {
    if (!graph) return [];
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    return graph.edges.map((e) => {
      const atCost = e.ref_basis !== "market";
      // Edge colour follows the step it feeds, so a losing step's inputs read as such.
      const tone = deltaTone(byId.get(e.target)?.value_added ?? null);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label: `${qty(e.quantity)}× · ${money(e.ref_total)}${atCost ? " *" : ""}`,
        className: `flow ${tone} ${atCost ? "at-cost" : ""}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 4,
      };
    });
  }, [graph]);

  const onNodeDragStop: OnNodeDrag<FlowNode> = useCallback(
    (_e, _node, dragged) =>
      setPlan((p) => ({
        ...p,
        positions: { ...p.positions, ...Object.fromEntries(dragged.map((n) => [n.id, n.position])) },
      })),
    [setPlan],
  );

  const relayout = useCallback(() => {
    setPlan((p) => ({ ...p, positions: {} }));
    setLayoutNonce((n) => n + 1);
  }, [setPlan]);

  const selected = graph?.nodes.find((n) => n.id === selectedId) ?? null;

  return (
    <div className="app">
      <Sidebar plan={plan} setPlan={setPlan} meta={meta} graph={graph} onRelayout={relayout} />
      <main className="canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeDragStop={onNodeDragStop}
          onNodeClick={(_e, n) => setSelectedId(n.id)}
          onPaneClick={() => setSelectedId(null)}
          nodesConnectable={false}
          edgesFocusable={false}
          minZoom={0.1}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} size={1} />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeStrokeWidth={2} />
        </ReactFlow>
        {plan.targets.length === 0 && (
          <div className="empty">
            <p>Add an item on the left to lay out its crafting chain.</p>
            <p className="muted">
              Each crafting step shows its <strong>value added</strong>: the AH value of what it makes, minus the
              value of what goes into it.
            </p>
          </div>
        )}
        {error && <div className="banner error">{error}</div>}
        {loading && <div className="banner loading">updating…</div>}
      </main>
      {selected && graph && <Details node={selected} graph={graph} onClose={() => setSelectedId(null)} />}
    </div>
  );
}
