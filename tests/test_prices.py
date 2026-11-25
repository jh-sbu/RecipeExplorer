from datetime import UTC, datetime, timedelta

from conftest import (
    BOLT,
    BOLT_SPELL,
    COPPER_BAR,
    GREATER_ESS,
    LESSER_ESS,
    LINEN,
    SHIRT,
    SHIRT_SPELL,
    SMELT,
    THREAD,
)

from recipe_explorer.prices import PriceResolver, PriceSnapshot, store_snapshots
from recipe_explorer.prices.adapters import ADAPTERS

REALM = "Testrealm"
NOW = datetime.now(UTC).isoformat(timespec="seconds")


def add(prices_conn, item_id, price, *, faction="", observed_at=NOW, source="test"):
    store_snapshots(
        prices_conn,
        [PriceSnapshot(item_id=item_id, observed_at=observed_at, market_value=price)],
        source=source, realm=REALM, faction=faction,
    )


def test_unpriceable_without_data(conn):
    q = PriceResolver(conn, realm=REALM).quote(LINEN)
    assert q.unit_cost is None and q.method == "unknown"


def test_vendor_price_is_per_unit(conn):
    q = PriceResolver(conn).quote(THREAD, 3)
    assert (q.method, q.unit_cost, q.total) == ("vendor", 10, 30)


def test_vendor_price_requires_vendor_items_listing(conn):
    # Copper Ore has a BuyPrice in the client but is not a vendor item.
    assert PriceResolver(conn).vendor_price(2770) is None


def test_crafting_recursion_picks_cheapest_path(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 500)  # AH bolt is pricier than crafting (2 * 50)
    q = PriceResolver(conn, realm=REALM).quote(SHIRT)
    assert q.method == "craft" and q.recipe_spell_id == SHIRT_SPELL
    assert q.unit_cost == 2 * 100 + 10
    bolt = next(c for c in q.components if c.item_id == BOLT)
    assert bolt.method == "craft" and bolt.options == {"market": 500, "craft": 100}
    assert bolt.components[0].quantity == 4  # 2 bolts * 2 linen


def test_market_wins_when_cheaper(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 80)
    q = PriceResolver(conn, realm=REALM).quote(BOLT)
    assert (q.method, q.unit_cost, q.components) == ("market", 80, [])


def test_methods_can_be_restricted(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    q = PriceResolver(conn, realm=REALM, methods=("market",)).quote(BOLT)
    assert q.method == "unknown"


def test_cycles_terminate(conn, prices_conn):
    add(prices_conn, LESSER_ESS, 100)
    q = PriceResolver(conn, realm=REALM).quote(GREATER_ESS)
    assert (q.method, q.unit_cost) == ("craft", 300)
    # And with no data at all, the essence loop resolves to unknown instead of recursing forever.
    assert PriceResolver(conn, realm="Elsewhere").quote(GREATER_ESS).method == "unknown"


def test_multi_output_recipe_divides_cost(conn, prices_conn):
    add(prices_conn, GREATER_ESS, 600)
    q = PriceResolver(conn, realm=REALM).quote(LESSER_ESS)
    assert (q.method, q.unit_cost) == ("craft", 200)


def test_latest_snapshot_and_max_age(conn, prices_conn):
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="seconds")
    add(prices_conn, COPPER_BAR, 300, observed_at=old)
    add(prices_conn, COPPER_BAR, 200)
    assert PriceResolver(conn, realm=REALM).market_price(COPPER_BAR) == 200
    prices_conn.execute("DELETE FROM price_snapshots WHERE observed_at = ?", (NOW,))
    prices_conn.commit()
    assert PriceResolver(conn, realm=REALM).market_price(COPPER_BAR) == 300
    assert PriceResolver(conn, realm=REALM, max_age=timedelta(days=1)).market_price(COPPER_BAR) is None


def test_faction_specific_beats_shared(conn, prices_conn):
    add(prices_conn, COPPER_BAR, 100, faction="")
    add(prices_conn, COPPER_BAR, 150, faction="horde")
    assert PriceResolver(conn, realm=REALM, faction="horde").market_price(COPPER_BAR) == 150
    assert PriceResolver(conn, realm=REALM, faction="alliance").market_price(COPPER_BAR) == 100


def test_profit(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 200)
    p = PriceResolver(conn, realm=REALM, ah_cut=0.05).profit(BOLT_SPELL)
    assert (p.revenue, p.cost.total, p.profit) == (190, 100, 90)


def test_profit_without_product_price(conn, prices_conn):
    add(prices_conn, 2770, 10)
    p = PriceResolver(conn, realm=REALM).profit(SMELT)
    assert p.cost.total == 10 and p.revenue is None and p.profit is None


def test_csv_adapter_and_idempotent_import(tmp_path, prices_conn):
    f = tmp_path / "prices.csv"
    f.write_text("item_id,min_buyout,market_value,quantity,observed_at\n"
                 f"{LINEN},40,45,200,2026-11-20T10:00:00+00:00\n{BOLT},,90,,2026-11-20T10:00:00+00:00\n")
    snaps = list(ADAPTERS["csv"](f).read())
    assert snaps[0] == PriceSnapshot(LINEN, "2026-11-20T10:00:00+00:00", 40, 45, 200)
    assert snaps[1].min_buyout is None and snaps[1].best_estimate == 90
    assert store_snapshots(prices_conn, snaps, source="csv", realm=REALM) == 2
    assert store_snapshots(prices_conn, snaps, source="csv", realm=REALM) == 0


def test_prices_survive_gamedata_rebuild(paths, prices_conn, manual_dir):
    from recipe_explorer.ingest.normalize import build_gamedata

    add(prices_conn, LINEN, 50)
    build_gamedata(paths["raw"], paths["gamedata"], build="1.0.1.test", manual_dir=manual_dir)
    assert prices_conn.execute("SELECT count(*) FROM price_snapshots").fetchone()[0] == 1
