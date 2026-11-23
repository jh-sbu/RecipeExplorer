import json
import threading
import urllib.request

import pytest
from conftest import BOLT, BOLT_SPELL, GREATER_ESS, LESSER_ESS, LINEN, SHIRT, SHIRT_SPELL, THREAD
from test_prices import REALM, add

from recipe_explorer.prices import Override, PriceResolver, build_chain
from recipe_explorer.ui.server import ApiError, api_graph, api_items, make_server


def chain(conn, targets, overrides=None, **kw):
    return build_chain(PriceResolver(conn, realm=REALM, **kw), targets, overrides)


def test_merges_shared_intermediates_and_sums_quantities(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    # Two targets that both need bolts: 3 shirts (6 bolts) and 4 bolts directly.
    g = chain(conn, [(SHIRT, 3), (BOLT, 4)])
    bolts = [n for n in g.nodes if n.item_id == BOLT]
    assert len(bolts) == 1
    bolt = bolts[0]
    assert bolt.kind == "step" and bolt.quantity == 10 and bolt.crafts == 10 and bolt.is_target
    assert g.node(str(LINEN)).quantity == 20
    assert g.targets == [str(SHIRT), str(BOLT)]
    # Consumers come before their inputs.
    ids = [n.id for n in g.nodes]
    assert ids.index(str(SHIRT)) < ids.index(str(BOLT)) < ids.index(str(LINEN))


def test_value_added_per_step(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 130)  # craft cost 100, so bolts are crafted
    add(prices_conn, SHIRT, 400)
    g = chain(conn, [(SHIRT, 1)])
    bolt, shirt = g.node(str(BOLT)), g.node(str(SHIRT))
    # Bolt step: 2 bolts worth 260 on the AH, from 4 linen worth 200.
    assert g.value_added(bolt) == {
        "output_value": 260, "input_value": 200, "value_added": 60, "value_added_pct": 0.3, "complete": True,
    }
    # Shirt step: inputs are valued at the AH (bolts 2 * 130), plus vendor thread (no AH price).
    va = g.value_added(shirt)
    assert (va["output_value"], va["input_value"], va["value_added"]) == (400, 270, 130)
    assert va["complete"] is False
    thread_edge = next(e for e in g.edges if e.item_id == THREAD)
    assert (thread_edge.ref_unit, thread_edge.ref_basis) == (10, "vendor")
    # Steps sum to final value minus raw inputs at their reference values (4 linen + 1 thread).
    assert sum(g.value_added(n)["value_added"] for n in (bolt, shirt)) == 400 - (4 * 50 + 10)
    assert shirt.unit_cost == 210  # what it actually costs, via cheapest paths


def test_unknown_market_leaves_value_added_unknown(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    g = chain(conn, [(SHIRT, 1)])
    va = g.value_added(g.node(str(SHIRT)))
    # No AH price for the shirt: nothing to compare against. Inputs fall back to their cost.
    assert va["value_added"] is None and va["output_value"] is None and va["input_value"] == 210
    assert g.node(str(SHIRT)).unit_cost == 210


def test_overrides_buy_and_recipe(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 500)
    g = chain(conn, [(SHIRT, 1)], {BOLT: Override("market")})
    bolt = g.node(str(BOLT))
    assert (bolt.kind, bolt.method, bolt.unit_cost, bolt.overridden) == ("source", "market", 500, True)
    assert str(LINEN) not in {n.id for n in g.nodes}  # no longer reached
    assert g.node(str(SHIRT)).unit_cost == 1010
    # Forcing a buy method with no price is shown as such rather than silently ignored.
    g = chain(conn, [(BOLT, 1)], {BOLT: Override("vendor")})
    assert (g.node(str(BOLT)).unit_cost, g.node(str(BOLT)).note) == (None, "no vendor price")
    # Forcing craft with an explicit recipe.
    g = chain(conn, [(BOLT, 1)], {BOLT: Override("craft", BOLT_SPELL)})
    assert g.node(str(BOLT)).recipe.spell_id == BOLT_SPELL


def test_craftable_but_unpriced_still_shows_chain(conn):
    g = chain(conn, [(SHIRT, 1)])
    shirt = g.node(str(SHIRT))
    assert shirt.kind == "step" and shirt.recipe.spell_id == SHIRT_SPELL and shirt.unit_cost is None
    assert {n.item_id for n in g.nodes} == {SHIRT, BOLT, LINEN, THREAD}


def test_cycles_become_bought_copies(conn, prices_conn):
    add(prices_conn, LESSER_ESS, 100)
    add(prices_conn, GREATER_ESS, 250)  # crafting 3 lesser (300) is dearer: buy
    g = chain(conn, [(LESSER_ESS, 3)], {LESSER_ESS: Override("craft"), GREATER_ESS: Override("craft")})
    # Lesser <- Greater (forced craft) <- Lesser again: that inner one is a bought copy.
    lesser, greater = g.node(str(LESSER_ESS)), g.node(str(GREATER_ESS))
    assert lesser.kind == "step" and greater.kind == "step"
    copy = g.node(f"{LESSER_ESS}:buy")
    assert copy.cycle_copy and copy.kind == "source" and copy.quantity == 3
    assert {(e.source, e.target) for e in g.edges} == {
        (f"{LESSER_ESS}:buy", str(GREATER_ESS)), (str(GREATER_ESS), str(LESSER_ESS)),
    }


def test_to_json_is_serializable(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, SHIRT, 400)
    data = json.loads(json.dumps(chain(conn, [(SHIRT, 2)]).to_json()))
    assert data["summary"]["target_market"] == 800 and data["summary"]["target_cost"] == 420
    assert data["summary"]["revenue_after_cut"] == 760 and data["summary"]["profit"] == 340
    assert {e["id"] for e in data["edges"]} >= {f"{BOLT}->{SHIRT}"}


def test_api_graph_validation(conn):
    with pytest.raises(ApiError, match="unknown item"):
        api_graph(conn, {"targets": [{"item_id": 999999, "quantity": 1}]})
    with pytest.raises(ApiError, match="override method"):
        api_graph(conn, {"targets": [], "overrides": {str(BOLT): {"method": "steal"}}})
    out = api_graph(conn, {"targets": [{"item_id": SHIRT, "quantity": 0}]})
    assert out["nodes"] == []


def test_api_items_puts_craftable_first(conn):
    rows = api_items(conn, "linen")
    assert [r["craftable"] for r in rows] == [1, 1, 0, 0]  # shirt, bolt, then cloth and pattern
    assert rows[0]["name"] == "Linen Shirt"  # starts with the query, then shorter names first


def test_http_server_roundtrip(paths, tmp_path, prices_conn):
    add(prices_conn, LINEN, 50)
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>t</title>")
    server = make_server("127.0.0.1", 0, gamedata_path=paths["gamedata"], prices_path=paths["prices"],
                         static_dir=static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        meta = json.load(urllib.request.urlopen(f"{base}/api/meta"))
        assert meta["realms"][0]["realm"] == REALM
        req = urllib.request.Request(
            f"{base}/api/graph", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"targets": [{"item_id": BOLT, "quantity": 2}], "realm": REALM}).encode(),
        )
        graph = json.load(urllib.request.urlopen(req))
        assert graph["summary"]["raw_cost"] == 200
        assert b"<title>t</title>" in urllib.request.urlopen(f"{base}/some/client/route").read()
        assert b"<title>t</title>" in urllib.request.urlopen(f"{base}/../../etc/passwd").read()
    finally:
        server.shutdown()
        server.server_close()


def test_targets_are_crafted_even_when_buying_is_cheaper(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 60)  # buying (60) beats crafting (100)
    assert chain(conn, [(BOLT, 1)]).node(str(BOLT)).method == "craft"
    # As an intermediate it is still bought, and an explicit override still wins.
    assert chain(conn, [(SHIRT, 1)]).node(str(BOLT)).method == "market"
    assert chain(conn, [(BOLT, 1)], {BOLT: Override("market")}).node(str(BOLT)).method == "market"
