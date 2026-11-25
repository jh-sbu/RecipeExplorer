// Mirrors recipe_explorer.prices.chain (ChainGraph.to_json). Money is copper.

export type Method = "craft" | "market" | "vendor" | "unknown";

export interface RecipeOption {
  spell_id: number;
  name: string;
  profession: string;
  product_count: number;
  unit_cost: number | null;
}

export interface ChainNode {
  id: string;
  item_id: number;
  name: string;
  quality: number | null;
  kind: "step" | "source";
  method: Method;
  options: Partial<Record<"market" | "vendor" | "craft", number>>;
  unit_cost: number | null;
  total_cost: number | null;
  market: number | null;
  quantity: number;
  is_target: boolean;
  overridden: boolean;
  cycle_copy: boolean;
  recipe: RecipeOption | null;
  recipes: RecipeOption[];
  crafts: number;
  note: string | null;
  // value added (steps only)
  output_value: number | null;
  input_value: number | null;
  value_added: number | null;
  value_added_pct: number | null;
  complete: boolean;
}

export interface ChainEdge {
  id: string;
  source: string;
  target: string;
  item_id: number;
  quantity: number;
  per_craft: number;
  ref_unit: number | null;
  ref_total: number | null;
  ref_basis: Method;
}

export interface Summary {
  raw_cost: number | null;
  target_cost: number | null;
  target_market: number | null;
  revenue_after_cut: number | null;
  profit: number | null;
  value_added_known: number;
  steps: number;
  steps_unvalued: number;
}

export interface Graph {
  nodes: ChainNode[];
  edges: ChainEdge[];
  targets: string[];
  summary: Summary;
}

export interface ItemHit {
  id: number;
  name: string;
  quality: number | null;
  craftable: number;
}

export interface Meta {
  meta: Record<string, string>;
  realms: { realm: string; faction: string; items: number; newest: string }[];
  professions: { id: number; name: string; category: "primary" | "secondary"; recipes: number }[];
}

export interface Override {
  method: "craft" | "market" | "vendor";
  recipe?: number;
}

export type DeltaMode = "total" | "per_unit" | "percent";

// Mirrors recipe_explorer.prices.uses (UseReport.to_json). Money is copper.
export interface UseReagent {
  item_id: number;
  name: string;
  count: number;
  unit_cost: number | null;
  method: Method;
}

export interface Use {
  spell_id: number;
  recipe: string;
  profession: string;
  consumes: number;
  product_item_id: number | null;
  product_name: string | null;
  product_quality: number | null;
  product_count: number;
  reagents: UseReagent[];
  other_cost: number | null;
  product_market: number | null;
  product_listings: number | null;
  product_observed_at: string | null;
  product_thin: boolean;
  product_vendor_sell: number | null;
  sale: "market" | "vendor" | "use" | "none";
  product_value: number | null;
  then: Use | null;
  value_per_cast: number | null;
  value_per_unit: number | null;
}

export interface UseReport {
  item_id: number;
  name: string;
  quality: number | null;
  depth: number;
  ah_cut: number;
  min_listings: number;
  market: number | null;
  listings: number | null;
  observed_at: string | null;
  thin: boolean;
  ah_sell: number | null;
  vendor_sell: number | null;
  best_sale: number | null;
  uses: Use[];
  professions: number[] | null;
  hidden: number; // direct uses left out by the profession filter
}

export interface UseSettings {
  depth: number;
  minListings: number;
  professions: number[]; // profession ids; empty = every profession
}

export interface Plan {
  version: 1;
  targets: { item_id: number; name: string; quality: number | null; quantity: number }[];
  overrides: Record<string, Override>; // keyed by item id
  settings: { realm: string; faction: string; maxAgeDays: number | null; ahCutPct: number };
  positions: Record<string, { x: number; y: number }>; // node id -> position the user dragged it to
  deltaMode: DeltaMode;
  uses: UseSettings;
}
