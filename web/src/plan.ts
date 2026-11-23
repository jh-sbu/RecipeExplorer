import type { Plan } from "./types";

const KEY = "rex.plan.v1";

export const emptyPlan = (): Plan => ({
  version: 1,
  targets: [],
  overrides: {},
  settings: { realm: "", faction: "", maxAgeDays: null, ahCutPct: 5 },
  positions: {},
  deltaMode: "total",
});

/** Accepts anything (localStorage, an imported file) and returns a well-formed plan. */
export function parsePlan(raw: unknown): Plan {
  const base = emptyPlan();
  if (!raw || typeof raw !== "object") return base;
  const p = raw as Partial<Plan>;
  if (p.version !== 1) throw new Error("not a recipe-explorer plan (version 1)");
  return {
    ...base,
    targets: Array.isArray(p.targets)
      ? p.targets.filter((t) => Number.isInteger(t?.item_id)).map((t) => ({
          item_id: t.item_id,
          name: String(t.name ?? `#${t.item_id}`),
          quality: t.quality ?? null,
          quantity: Number(t.quantity) > 0 ? Number(t.quantity) : 1,
        }))
      : [],
    overrides: p.overrides && typeof p.overrides === "object" ? p.overrides : {},
    settings: { ...base.settings, ...(p.settings ?? {}) },
    positions: p.positions && typeof p.positions === "object" ? p.positions : {},
    deltaMode: p.deltaMode ?? base.deltaMode,
  };
}

export function loadPlan(): Plan {
  try {
    const s = localStorage.getItem(KEY);
    return s ? parsePlan(JSON.parse(s)) : emptyPlan();
  } catch {
    return emptyPlan();
  }
}

export function savePlan(plan: Plan): void {
  localStorage.setItem(KEY, JSON.stringify(plan));
}

export function downloadPlan(plan: Plan): void {
  const name = plan.targets.map((t) => t.name).join("+").slice(0, 60) || "plan";
  const url = URL.createObjectURL(new Blob([JSON.stringify(plan, null, 2)], { type: "application/json" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${name}.rexplan.json` });
  a.click();
  URL.revokeObjectURL(url);
}
