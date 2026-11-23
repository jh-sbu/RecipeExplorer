import { METHOD_LABEL, money, QUALITY_COLOR, qty } from "./format";
import type { ChainNode, Graph } from "./types";

/** Breakdown for the selected node: every price option, recipes, and the value-added sum. */
export function Details({ node, graph, onClose }: { node: ChainNode; graph: Graph; onClose: () => void }) {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const inputs = graph.edges.filter((e) => e.target === node.id);
  const consumers = graph.edges.filter((e) => e.source === node.id);

  return (
    <aside className="details">
      <header>
        <h2 style={{ color: QUALITY_COLOR[node.quality ?? 1] }}>{node.name}</h2>
        <button className="icon" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </header>
      <div className="muted small">
        item {node.item_id} · ×{qty(node.quantity)} · {METHOD_LABEL[node.method]}
      </div>

      <h3>Ways to obtain (per item)</h3>
      <table>
        <tbody>
          {(["market", "vendor", "craft"] as const).map((m) => (
            <tr key={m} className={node.method === m ? "chosen" : ""}>
              <td>{METHOD_LABEL[m]}</td>
              <td className="num">{money(node.options[m])}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {node.recipes.length > 0 && (
        <>
          <h3>Recipes</h3>
          <table>
            <tbody>
              {node.recipes.map((r) => (
                <tr key={r.spell_id} className={node.recipe?.spell_id === r.spell_id ? "chosen" : ""}>
                  <td>
                    {r.name}
                    <div className="muted small">
                      {r.profession}
                      {r.product_count !== 1 && ` · makes ${qty(r.product_count)}`}
                    </div>
                  </td>
                  <td className="num">{money(r.unit_cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {node.kind === "step" && (
        <>
          <h3>Value added by this step</h3>
          <table>
            <tbody>
              <tr>
                <td>Output on AH ({qty(node.quantity)} × {money(node.market)})</td>
                <td className="num">{money(node.output_value)}</td>
              </tr>
              {inputs.map((e) => (
                <tr key={e.id}>
                  <td>
                    − {qty(e.quantity)} × {byId.get(e.source)?.name}
                    <div className="muted small">
                      {money(e.ref_unit)} each, {e.ref_basis === "market" ? "AH price" : `at cost (${METHOD_LABEL[e.ref_basis]})`}
                    </div>
                  </td>
                  <td className="num">{money(e.ref_total)}</td>
                </tr>
              ))}
              <tr className="total">
                <td>Value added</td>
                <td className="num">{money(node.value_added, { signed: true })}</td>
              </tr>
            </tbody>
          </table>
          {!node.complete && <p className="muted small">Inputs marked “at cost” have no AH price.</p>}
        </>
      )}

      {consumers.length > 0 && (
        <>
          <h3>Used by</h3>
          <ul className="plain">
            {consumers.map((e) => (
              <li key={e.id}>
                {qty(e.quantity)} → {byId.get(e.target)?.name}
              </li>
            ))}
          </ul>
        </>
      )}
      {node.note && <p className="note">{node.note}</p>}
    </aside>
  );
}
