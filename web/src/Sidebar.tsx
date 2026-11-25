import { useEffect, useRef, useState } from "react";
import { searchItems } from "./api";
import { DELTA_MODE_LABEL, money, QUALITY_COLOR } from "./format";
import { downloadPlan, parsePlan } from "./plan";
import type { DeltaMode, Graph, ItemHit, Meta, Plan } from "./types";

type Props = {
  plan: Plan;
  setPlan: (fn: (p: Plan) => Plan) => void;
  meta: Meta | null;
  graph: Graph | null;
  onRelayout: () => void;
};

function TargetSearch({ onPick }: { onPick: (hit: ItemHit) => void }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<ItemHit[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (q.trim().length < 2) return setHits([]);
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      searchItems(q, ctrl.signal)
        .then((h) => (setHits(h), setActive(0)))
        .catch(() => {});
    }, 150);
    return () => (clearTimeout(t), ctrl.abort());
  }, [q]);

  const pick = (hit: ItemHit) => {
    onPick(hit);
    setQ("");
    setHits([]);
  };

  return (
    <div className="search">
      <input
        type="search"
        placeholder="Add an item to make…"
        value={q}
        onChange={(e) => (setQ(e.target.value), setOpen(true))}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, hits.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter" && hits[active]) pick(hits[active]);
          else return;
          e.preventDefault();
        }}
        aria-label="Search items"
      />
      {open && hits.length > 0 && (
        <ul className="search-results" role="listbox">
          {hits.map((h, i) => (
            <li
              key={h.id}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : ""}
              onMouseDown={(e) => (e.preventDefault(), pick(h))}
            >
              <span style={{ color: QUALITY_COLOR[h.quality ?? 1] }}>{h.name}</span>
              {!h.craftable && <span className="muted"> · not craftable</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="summary-row">
      <span>{label}</span>
      {strong ? <strong>{value}</strong> : <span>{value}</span>}
    </div>
  );
}

/** "My professions": limits the Use tab to recipes you can craft. */
function MyProfessions({ meta, mine, onChange }: {
  meta: Meta | null; mine: number[]; onChange: (next: number[]) => void;
}) {
  if (!meta) return null;
  const sorted = [...meta.professions].sort(
    (a, b) => Number(a.category !== "primary") - Number(b.category !== "primary") || a.name.localeCompare(b.name),
  );
  const toggle = (id: number, on: boolean) => onChange(on ? [...mine.filter((x) => x !== id), id] : mine.filter((x) => x !== id));
  return (
    <section>
      <h2>My professions</h2>
      <p className="muted small">
        {mine.length
          ? "The Use tab only shows recipes of these, at any skill level."
          : "None picked: the Use tab shows every profession's recipes."}
      </p>
      <ul className="plain professions">
        {sorted.map((p) => (
          <li key={p.id}>
            <label title={`${p.recipes} recipes`}>
              <input type="checkbox" checked={mine.includes(p.id)} onChange={(e) => toggle(p.id, e.target.checked)} />
              {p.name}
            </label>
          </li>
        ))}
      </ul>
      {mine.length > 0 && (
        <button onClick={() => onChange([])}>Show all professions</button>
      )}
    </section>
  );
}

export function Sidebar({ plan, setPlan, meta, graph, onRelayout }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const s = graph?.summary;
  const realmKey = `${plan.settings.realm}|${plan.settings.faction}`;

  return (
    <aside className="sidebar">
      <h1>Recipe chains</h1>
      {meta && <div className="muted small">build {meta.meta.build ?? "?"}</div>}

      <section>
        <h2>Make</h2>
        <TargetSearch
          onPick={(hit) =>
            setPlan((p) =>
              p.targets.some((t) => t.item_id === hit.id)
                ? p
                : { ...p, targets: [...p.targets, { item_id: hit.id, name: hit.name, quality: hit.quality, quantity: 1 }] },
            )
          }
        />
        {plan.targets.length === 0 && <p className="muted small">Search for an item to see its crafting chain.</p>}
        <ul className="targets">
          {plan.targets.map((t) => (
            <li key={t.item_id}>
              <input
                type="number"
                min={1}
                step={1}
                value={t.quantity}
                aria-label={`Quantity of ${t.name}`}
                onChange={(e) => {
                  const v = Math.max(1, Number(e.target.value) || 1);
                  setPlan((p) => ({
                    ...p,
                    targets: p.targets.map((x) => (x.item_id === t.item_id ? { ...x, quantity: v } : x)),
                  }));
                }}
              />
              <span className="target-name" style={{ color: QUALITY_COLOR[t.quality ?? 1] }}>
                {t.name}
              </span>
              <button
                className="icon"
                title="Remove"
                aria-label={`Remove ${t.name}`}
                onClick={() => setPlan((p) => ({ ...p, targets: p.targets.filter((x) => x.item_id !== t.item_id) }))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Prices</h2>
        <label>
          Realm
          <select
            value={realmKey}
            onChange={(e) => {
              const [realm, faction] = e.target.value.split("|");
              setPlan((p) => ({ ...p, settings: { ...p.settings, realm, faction } }));
            }}
          >
            <option value="|">None (vendor prices only)</option>
            {meta?.realms.map((r) => (
              <option key={`${r.realm}|${r.faction}`} value={`${r.realm}|${r.faction}`}>
                {r.realm}
                {r.faction ? ` (${r.faction})` : ""} · {r.items} items
              </option>
            ))}
          </select>
        </label>
        {meta && meta.realms.length === 0 && (
          <p className="muted small">
            No AH data imported yet. <code>rex prices import csv …</code>
          </p>
        )}
        <div className="two">
          <label>
            Max age (days)
            <input
              type="number"
              min={0}
              placeholder="any"
              value={plan.settings.maxAgeDays ?? ""}
              onChange={(e) =>
                setPlan((p) => ({
                  ...p,
                  settings: { ...p.settings, maxAgeDays: e.target.value ? Number(e.target.value) : null },
                }))
              }
            />
          </label>
          <label>
            AH cut %
            <input
              type="number"
              min={0}
              max={100}
              value={plan.settings.ahCutPct}
              onChange={(e) =>
                setPlan((p) => ({ ...p, settings: { ...p.settings, ahCutPct: Number(e.target.value) || 0 } }))
              }
            />
          </label>
        </div>
      </section>

      <MyProfessions
        meta={meta}
        mine={plan.uses.professions}
        onChange={(professions) => setPlan((p) => ({ ...p, uses: { ...p.uses, professions } }))}
      />

      <section>
        <h2>Value added shown as</h2>
        <div className="segmented" role="radiogroup">
          {(Object.keys(DELTA_MODE_LABEL) as DeltaMode[]).map((m) => (
            <button
              key={m}
              role="radio"
              aria-checked={plan.deltaMode === m}
              className={plan.deltaMode === m ? "on" : ""}
              onClick={() => setPlan((p) => ({ ...p, deltaMode: m }))}
            >
              {DELTA_MODE_LABEL[m]}
            </button>
          ))}
        </div>
        <div className="legend small">
          <span className="swatch pos" /> ▲ step adds value <span className="swatch neg" /> ▼ step loses value
          <span className="swatch zero" /> no AH price for the output
          <div>
            <span className="dash" /> input with no AH price, valued at its cost (marked *)
          </div>
        </div>
      </section>

      {s && graph!.nodes.length > 0 && (
        <section>
          <h2>Plan totals</h2>
          <Row label="Bought materials" value={money(s.raw_cost)} />
          <Row label="Cost of targets" value={money(s.target_cost)} />
          <Row label="Targets on AH" value={money(s.target_market)} />
          <Row label="After AH cut" value={money(s.revenue_after_cut)} />
          <Row label="Profit" value={money(s.profit, { signed: true })} strong />
          <Row
            label={`Value added (${s.steps - s.steps_unvalued}/${s.steps} steps)`}
            value={money(s.value_added_known, { signed: true })}
          />
        </section>
      )}

      <section>
        <h2>Plan</h2>
        <div className="buttons">
          <button onClick={onRelayout}>Re-layout</button>
          <button
            disabled={!Object.keys(plan.overrides).length}
            onClick={() => setPlan((p) => ({ ...p, overrides: {} }))}
          >
            Reset choices
          </button>
          <button onClick={() => downloadPlan(plan)}>Export</button>
          <button onClick={() => fileRef.current?.click()}>Import</button>
          <button
            onClick={() => confirm("Clear the whole plan?") && setPlan((p) => ({ ...p, targets: [], overrides: {}, positions: {} }))}
          >
            Clear
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          hidden
          onChange={async (e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            try {
              const next = parsePlan(JSON.parse(await f.text()));
              setPlan(() => next);
            } catch (err) {
              alert(`Could not import plan: ${(err as Error).message}`);
            }
          }}
        />
      </section>
    </aside>
  );
}
