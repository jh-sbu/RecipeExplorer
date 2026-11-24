import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import { delta, METHOD_LABEL, money, QUALITY_COLOR, qty } from "./format";
import type { ChainNode, DeltaMode, Override } from "./types";

export type ItemNodeData = {
  node: ChainNode;
  deltaMode: DeltaMode;
  override: Override | undefined;
  hasConsumers: boolean;
  onOverride: (itemId: number, o: Override | null) => void;
  onShowUses: (nodeId: string) => void;
};
export type FlowNode = Node<ItemNodeData, "item">;

/** Encodes polarity: blue above zero, red below, grey for zero or unknown. Never colour alone. */
export function deltaTone(value: number | null): "pos" | "neg" | "zero" {
  if (value == null || value === 0) return "zero";
  return value > 0 ? "pos" : "neg";
}

function overrideValue(o: Override | undefined): string {
  if (!o) return "auto";
  return o.method === "craft" ? `craft:${o.recipe ?? ""}` : o.method;
}

function parseOverride(v: string): Override | null {
  if (v === "auto") return null;
  if (v.startsWith("craft:")) {
    const spell = Number(v.slice(6));
    return spell ? { method: "craft", recipe: spell } : { method: "craft" };
  }
  return { method: v as Override["method"] };
}

function MethodSelect({ data }: { data: ItemNodeData }) {
  const n = data.node;
  if (n.cycle_copy) return null;
  const current = overrideValue(data.override);
  const recipes = n.recipes;
  return (
    <select
      className="nodrag method-select"
      value={current}
      aria-label={`How to obtain ${n.name}`}
      onChange={(e) => data.onOverride(n.item_id, parseOverride(e.target.value))}
    >
      <option value="auto">Auto: cheapest</option>
      {recipes.map((r) => (
        <option key={r.spell_id} value={`craft:${r.spell_id}`}>
          Craft{recipes.length > 1 ? `: ${r.name} (${r.profession})` : ""} · {money(r.unit_cost)}/ea
        </option>
      ))}
      <option value="market">Buy on AH · {money(n.options.market)}</option>
      {(n.options.vendor != null || data.override?.method === "vendor") && (
        <option value="vendor">Buy from vendor · {money(n.options.vendor)}</option>
      )}
    </select>
  );
}

function ItemNodeImpl({ data, selected }: NodeProps<FlowNode>) {
  const n = data.node;
  const d = delta(n, data.deltaMode);
  const tone = deltaTone(d.value);
  // Avoid bare "source"/"target" classes: React Flow looks up handles by those names.
  const cls = ["item-node", `kind-${n.kind}`, selected && "is-selected", n.is_target && "is-target", n.cycle_copy && "is-copy"]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cls}>
      {n.kind === "step" && <Handle type="target" position={Position.Left} isConnectable={false} />}
      {data.hasConsumers && <Handle type="source" position={Position.Right} isConnectable={false} />}

      <header>
        <span className="item-name" style={{ color: QUALITY_COLOR[n.quality ?? 1] }} title={`item ${n.item_id}`}>
          {n.name}
        </span>
        <span className="item-qty">×{qty(n.quantity)}</span>
      </header>

      <div className="sub">
        {n.kind === "step" && n.recipe ? (
          <>
            {n.recipe.profession}
            {n.crafts !== n.quantity && <> · {qty(n.crafts)} casts</>}
          </>
        ) : (
          <span className={`method-tag ${n.method}`}>{METHOD_LABEL[n.method]}</span>
        )}
        {n.is_target && <span className="target-tag">target</span>}
        {n.overridden && <span className="override-tag" title="You chose how to obtain this">manual</span>}
        <button
          className="nodrag uses-btn"
          title="Most profitable ways to use this item"
          onClick={(e) => (e.stopPropagation(), data.onShowUses(n.id))}
        >
          uses ⇢
        </button>
      </div>

      <div className="prices">
        <div>
          <span className="lbl">cost</span> {money(n.unit_cost)}
          <span className="lbl">/ea</span>
        </div>
        <div>
          <span className="lbl">AH</span> {money(n.market)}
        </div>
      </div>

      {n.kind === "step" && (
        <div className={`delta ${tone}`} title="AH value of the output minus the inputs' value">
          <span className="lbl">value added</span>
          <strong>
            {tone === "pos" ? "▲ " : tone === "neg" ? "▼ " : ""}
            {d.text}
          </strong>
          {!n.complete && n.value_added != null && (
            <span className="lbl" title="Some inputs have no AH price and are valued at cost">
              *
            </span>
          )}
        </div>
      )}

      <MethodSelect data={data} />
      {n.note && <div className="note">{n.note}</div>}
    </div>
  );
}

export const ItemNode = memo(ItemNodeImpl);
