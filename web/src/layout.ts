import type { ELK } from "elkjs/lib/elk-api";
import type { Graph } from "./types";

// ELK is most of the bundle; load it on first layout rather than up front.
let elkPromise: Promise<ELK> | null = null;
const getElk = () =>
  (elkPromise ??= import("elkjs/lib/elk.bundled.js").then(({ default: ElkCtor }) => new ElkCtor()));

export const NODE_WIDTH = 260;
const EST_HEIGHT = { step: 170, source: 110 };

/** Left-to-right layered layout: raw materials on the left, targets on the right. */
export async function layout(
  graph: Graph,
  measured: Map<string, { width: number; height: number }>,
): Promise<Record<string, { x: number; y: number }>> {
  const res = await (await getElk()).layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "150",
      "elk.spacing.nodeNode": "28",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.separateConnectedComponents": "true",
      "elk.spacing.componentComponent": "60",
    },
    children: graph.nodes.map((n) => ({
      id: n.id,
      width: measured.get(n.id)?.width ?? NODE_WIDTH,
      height: measured.get(n.id)?.height ?? EST_HEIGHT[n.kind],
    })),
    edges: graph.edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  return Object.fromEntries((res.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]));
}
