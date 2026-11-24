import { Fragment, useEffect, useState } from "react";
import { getUses } from "./api";
import { ago, METHOD_LABEL, money, QUALITY_COLOR, qty } from "./format";
import { deltaTone } from "./ItemNode";
import type { ChainNode, Override, Plan, Use, UseReport } from "./types";

const SALE_LABEL: Record<Use["sale"], string> = {
  market: "sold on AH",
  vendor: "sold to vendor",
  use: "used further",
  none: "no sale value",
};

const steps = (u: Use): Use[] => (u.then ? [u, ...steps(u.then)] : [u]);

/** What to add to the plan so it makes the last product of `use` from `have` units of the item. */
function planFor(use: Use, have: number) {
  let avail = Math.max(have, 1);
  const overrides: Record<string, Override> = {};
  const path = steps(use);
  for (const s of path) {
    if (s.product_item_id == null) return null;
    const casts = Math.max(1, Math.floor(avail / s.consumes));
    avail = casts * s.product_count;
    overrides[s.product_item_id] = { method: "craft", recipe: s.spell_id };
  }
  const last = path[path.length - 1];
  return {
    target: {
      item_id: last.product_item_id!,
      name: last.product_name ?? `#${last.product_item_id}`,
      quality: last.product_quality,
      quantity: Math.max(1, Math.round(avail)),
    },
    overrides,
  };
}

function Liquidity({ market, listings, observedAt, thin }: {
  market: number | null; listings: number | null; observedAt: string | null; thin: boolean;
}) {
  if (market == null) return <span className="muted">no AH price</span>;
  return (
    <span className={thin ? "thin" : "muted"} title={thin ? "Fewer listings than your minimum: AH price ignored" : undefined}>
      AH {money(market)} · {listings ?? "?"} listed · {ago(observedAt)}
      {thin && " · too thin"}
    </span>
  );
}

/** Per-cast arithmetic for one step of a use. */
function StepBreakdown({ s, input }: { s: Use; input: string }) {
  const out = s.product_value == null ? null : s.product_value * s.product_count;
  return (
    <div className="step">
      <div>
        <strong>{s.recipe}</strong> <span className="muted">({s.profession}) · per cast</span>
      </div>
      <table>
        <tbody>
          <tr>
            <td>
              {qty(s.product_count)} × {s.product_name ?? "(no item)"}, {SALE_LABEL[s.sale]}
              {s.product_item_id != null && (
                <div className="small">
                  <Liquidity market={s.product_market} listings={s.product_listings}
                    observedAt={s.product_observed_at} thin={s.product_thin} />
                  {s.product_vendor_sell != null && <span className="muted"> · vendor {money(s.product_vendor_sell)}</span>}
                </div>
              )}
            </td>
            <td className="num">{money(out)}</td>
          </tr>
          {s.reagents.map((g) => (
            <tr key={g.item_id}>
              <td>
                − {qty(g.count)} × {g.name}
                <div className="muted small">{money(g.unit_cost)} each, {METHOD_LABEL[g.method]}</div>
              </td>
              <td className="num">{money(g.unit_cost == null ? null : g.unit_cost * g.count)}</td>
            </tr>
          ))}
          <tr className="total">
            <td>÷ {qty(s.consumes)} {input}</td>
            <td className="num">{money(s.value_per_unit)} /ea</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function UseRow({ u, report, node, open, onToggle, onPlan }: {
  u: Use; report: UseReport; node: ChainNode; open: boolean; onToggle: () => void;
  onPlan: (u: Use) => void;
}) {
  const path = steps(u);
  const last = path[path.length - 1];
  const gain = u.value_per_unit != null && report.best_sale != null ? u.value_per_unit - report.best_sale : null;
  const tone = deltaTone(gain);
  const plan = planFor(u, node.quantity);
  return (
    <>
      <tr className={`use-row ${u.value_per_unit == null ? "unvalued" : ""}`} onClick={onToggle}>
        <td>
          <button className="disclose" aria-expanded={open} aria-label={`Details for ${u.recipe}`}>
            {open ? "▾" : "▸"}
          </button>
          {path.map((s, i) => (
            <Fragment key={s.spell_id}>
              {i > 0 && <span className="muted"> → </span>}
              <span style={{ color: QUALITY_COLOR[s.product_quality ?? 1] }}>{s.product_name ?? s.recipe}</span>
            </Fragment>
          ))}
          <div className="muted small">
            {u.profession} · uses {qty(u.consumes)}
            {last.sale === "market" || last.sale === "vendor" ? ` · then ${SALE_LABEL[last.sale]}` : ""}
            {last.product_thin && <span className="thin"> · thin market</span>}
          </div>
        </td>
        <td className="num">
          {money(u.value_per_unit)}
          {gain != null && (
            <div className={`gain ${tone}`} title="Compared with selling it as it is">
              {tone === "pos" ? "▲ " : tone === "neg" ? "▼ " : ""}
              {money(gain, { signed: true })}
            </div>
          )}
        </td>
      </tr>
      {open && (
        <tr className="breakdown">
          <td colSpan={2}>
            {path.map((s, i) => (
              <StepBreakdown key={s.spell_id} s={s} input={i === 0 ? report.name : (path[i - 1].product_name ?? "")} />
            ))}
            {plan && (
              <button
                onClick={() => onPlan(u)}
                title="Adds the final product as a target and sets each step to craft with these recipes"
              >
                Plan this: {plan.target.quantity} × {plan.target.name}
              </button>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

/** "What's the most profitable way to use this item?": its uses, ranked by value per unit. */
export function UsesPanel({ node, plan, setPlan }: {
  node: ChainNode; plan: Plan; setPlan: (fn: (p: Plan) => Plan) => void;
}) {
  const [report, setReport] = useState<UseReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  const key = JSON.stringify([node.item_id, plan.settings, plan.uses]);
  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    getUses(node.item_id, plan, ctrl.signal)
      .then((r) => (setReport(r), setError(null)))
      .catch((e) => e.name !== "AbortError" && setError(e.message))
      .finally(() => !ctrl.signal.aborted && setLoading(false));
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => setOpen(null), [node.item_id]);

  const setUses = (patch: Partial<Plan["uses"]>) => setPlan((p) => ({ ...p, uses: { ...p.uses, ...patch } }));

  const onPlan = (u: Use) => {
    const r = planFor(u, node.quantity);
    if (!r) return;
    setPlan((p) => {
      const exists = p.targets.some((t) => t.item_id === r.target.item_id);
      return {
        ...p,
        overrides: { ...p.overrides, ...r.overrides },
        targets: exists
          ? p.targets.map((t) => (t.item_id === r.target.item_id ? { ...t, quantity: t.quantity + r.target.quantity } : t))
          : [...p.targets, r.target],
      };
    });
  };

  const current = report?.item_id === node.item_id ? report : null;
  const makesItem = current?.uses.filter((u) => u.product_item_id != null) ?? [];
  const noItem = current?.uses.filter((u) => u.product_item_id == null) ?? [];

  return (
    <div className="uses">
      <div className="two">
        <div>
          <div className="small muted">Steps to follow</div>
          <div className="segmented">
            {[1, 2, 3].map((d) => (
              <button key={d} className={plan.uses.depth === d ? "on" : ""} onClick={() => setUses({ depth: d })}>
                {d}
              </button>
            ))}
          </div>
        </div>
        <label title="AH prices seen with fewer listings than this are ignored as sale values">
          Min listings
          <input
            type="number"
            min={0}
            step={1}
            value={plan.uses.minListings}
            onChange={(e) => setUses({ minListings: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
          />
        </label>
      </div>

      {error && <p className="note">{error}</p>}
      {!current && !error && <p className="muted small">{loading ? "Loading…" : ""}</p>}
      {current && (
        <>
          <h3>Sell as it is (per unit)</h3>
          <table>
            <tbody>
              <tr className={current.best_sale != null && current.best_sale === current.ah_sell ? "chosen" : ""}>
                <td>
                  AH, after {Math.round(current.ah_cut * 100)}% cut
                  <div className="small">
                    <Liquidity market={current.market} listings={current.listings}
                      observedAt={current.observed_at} thin={current.thin} />
                  </div>
                </td>
                <td className="num">{money(current.ah_sell)}</td>
              </tr>
              <tr className={current.best_sale != null && current.best_sale !== current.ah_sell ? "chosen" : ""}>
                <td>Vendor</td>
                <td className="num">{money(current.vendor_sell)}</td>
              </tr>
            </tbody>
          </table>

          <h3>
            Uses: value per {current.name}
            {loading && <span className="muted"> · updating…</span>}
          </h3>
          {makesItem.length === 0 && <p className="muted small">Not used in any recipe that makes an item.</p>}
          {makesItem.length > 0 && (
            <table className="use-table">
              <tbody>
                {makesItem.map((u) => (
                  <UseRow key={u.spell_id} u={u} report={current} node={node} open={open === u.spell_id}
                    onToggle={() => setOpen(open === u.spell_id ? null : u.spell_id)} onPlan={onPlan} />
                ))}
              </tbody>
            </table>
          )}
          {makesItem.some((u) => u.value_per_unit == null) && (
            <p className="muted small">Greyed-out uses have a product or reagent with no price.</p>
          )}
          {noItem.length > 0 && (
            <details className="no-item">
              <summary className="muted small">{noItem.length} recipe(s) that make no item (enchants etc.)</summary>
              <ul className="plain small">
                {noItem.map((u) => (
                  <li key={u.spell_id}>
                    {u.recipe} <span className="muted">· {u.profession} · uses {qty(u.consumes)}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}
