import type { Graph, ItemHit, Meta, Plan, UseReport } from "./types";

async function json<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`);
  return body as T;
}

export const getMeta = () => fetch("api/meta").then(json<Meta>);

export const searchItems = (q: string, signal?: AbortSignal) =>
  fetch(`api/items?q=${encodeURIComponent(q)}`, { signal }).then(json<ItemHit[]>);

export const getGraph = (plan: Plan, signal?: AbortSignal) =>
  fetch("api/graph", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      targets: plan.targets.map((t) => ({ item_id: t.item_id, quantity: t.quantity })),
      overrides: plan.overrides,
      realm: plan.settings.realm || null,
      faction: plan.settings.faction,
      max_age_days: plan.settings.maxAgeDays,
      ah_cut: plan.settings.ahCutPct / 100,
    }),
  }).then(json<Graph>);

export const getUses = (itemId: number, plan: Plan, signal?: AbortSignal) => {
  const s = plan.settings;
  const q = new URLSearchParams({
    item_id: String(itemId),
    faction: s.faction,
    ah_cut: String(s.ahCutPct / 100),
    depth: String(plan.uses.depth),
    min_listings: String(plan.uses.minListings),
  });
  if (s.realm) q.set("realm", s.realm);
  if (s.maxAgeDays) q.set("max_age_days", String(s.maxAgeDays));
  return fetch(`api/uses?${q}`, { signal }).then(json<UseReport>);
};
