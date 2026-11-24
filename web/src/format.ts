import type { ChainNode, DeltaMode } from "./types";

/** Copper -> "1g 2s 3c", same as the CLI's fmt_money. */
export function money(copper: number | null | undefined, { signed = false } = {}): string {
  if (copper == null) return "?";
  const c = Math.round(copper);
  const sign = c < 0 ? "−" : signed && c > 0 ? "+" : "";
  const abs = Math.abs(c);
  const g = Math.floor(abs / 10000);
  const s = Math.floor((abs % 10000) / 100);
  const parts = [];
  if (g) parts.push(`${g}g`);
  if (s || g) parts.push(`${s}s`);
  parts.push(`${abs % 100}c`);
  return sign + parts.join(" ");
}

export const qty = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, ""));

export const METHOD_LABEL: Record<string, string> = {
  craft: "Crafted",
  market: "AH",
  vendor: "Vendor",
  unknown: "No price",
};

// WoW item quality colours, used for item names only.
export const QUALITY_COLOR = ["#9d9d9d", "#f0efec", "#1eff00", "#4a9cff", "#c86eff", "#ff8000", "#e6cc80"];

/** The value-added figure a node shows, in the chosen mode. */
export function delta(node: ChainNode, mode: DeltaMode): { value: number | null; text: string } {
  const va = node.value_added;
  if (va == null) return { value: null, text: "?" };
  if (mode === "percent") {
    const p = node.value_added_pct;
    if (p == null) return { value: va, text: "n/a" };
    const pct = Math.round(p * 100);
    return { value: va, text: `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct)}%` };
  }
  if (mode === "per_unit") {
    const per = node.quantity ? va / node.quantity : 0;
    return { value: va, text: `${money(per, { signed: true })} / item` };
  }
  return { value: va, text: money(va, { signed: true }) };
}

export const DELTA_MODE_LABEL: Record<DeltaMode, string> = {
  total: "Total for the step",
  per_unit: "Per item made",
  percent: "% over inputs",
};

/** ISO time -> "3h ago" / "2d ago". */
export function ago(iso: string | null): string {
  if (!iso) return "?";
  const mins = (Date.now() - new Date(iso).getTime()) / 60000;
  if (!Number.isFinite(mins)) return "?";
  if (mins < 60) return `${Math.max(0, Math.round(mins))}m ago`;
  if (mins < 60 * 48) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}
