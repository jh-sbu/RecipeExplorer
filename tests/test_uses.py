import json
import sqlite3

import pytest
from conftest import BOLT, BOLT_SPELL, ENCHANT, ESS_UP, GREATER_ESS, LESSER_ESS, LINEN, SHIRT, SHIRT_SPELL, THREAD
from test_prices import NOW, REALM, add

from recipe_explorer.prices import PriceResolver, PriceSnapshot, find_uses, store_snapshots
from recipe_explorer.ui.server import ApiError, api_meta, api_uses

# Vendor sell prices in the fixture are BuyPrice // 4: linen 13, bolt 25, shirt 75. Essences 0.


def uses(conn, item_id, depth=2, min_listings=0):
    return find_uses(PriceResolver(conn, realm=REALM), item_id, depth, min_listings)


def test_single_step_value_per_unit(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 130)
    r = uses(conn, LINEN, depth=1)
    assert (r.ah_sell, r.vendor_sell, r.best_sale) == (48, 13, 48)  # 50 after a 5% cut
    # The SoD recipe that also uses linen is incomplete, so it's not listed.
    [u] = r.uses
    assert (u.spell_id, u.consumes, u.product_item_id, u.reagents) == (BOLT_SPELL, 2, BOLT, [])
    # One bolt sells for 124 after the cut, and takes 2 linen: 62 per linen.
    assert (u.sale, u.product_value, u.value_per_cast, u.value_per_unit) == ("market", 124, 124, 62)
    assert u.then is None and u.product_market == 130


def test_two_steps_beat_selling_the_intermediate(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 130)
    add(prices_conn, SHIRT, 1000)
    [u] = uses(conn, LINEN).uses
    # A bolt is worth more as half a shirt: (950 - 10 thread) / 2 = 470, versus 124 sold.
    assert (u.sale, u.product_value) == ("use", 470)
    assert u.value_per_unit == 235
    shirt = u.then
    assert (shirt.spell_id, shirt.consumes, shirt.other_cost, shirt.sale) == (SHIRT_SPELL, 2, 10, "market")
    assert [(g.item_id, g.unit_cost, g.method) for g in shirt.reagents] == [(THREAD, 10, "vendor")]
    assert [s.product_item_id for s in u.steps] == [BOLT, SHIRT]
    # At depth 1 the shirt isn't considered.
    assert uses(conn, LINEN, depth=1).uses[0].value_per_unit == 62


def test_vendor_sell_counts_without_ah_data(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    [u] = uses(conn, BOLT, depth=1).uses
    # No AH price for the shirt: it's worth its vendor price, 75, less 10 for thread.
    assert (u.sale, u.product_value, u.value_per_unit) == ("vendor", 75, 32)
    assert u.product_market is None


def test_cycles_are_skipped_and_enchants_listed_last(conn, prices_conn):
    add(prices_conn, LESSER_ESS, 100)
    add(prices_conn, GREATER_ESS, 250)
    r = uses(conn, LESSER_ESS, depth=3)
    assert [u.spell_id for u in r.uses] == [ESS_UP, ENCHANT]
    up, enchant = r.uses
    # Greater -> lesser would loop back, so the greater essence is just sold: 238 / 3.
    assert (up.sale, up.then, up.value_per_unit) == ("market", None, 79)
    assert (enchant.product_item_id, enchant.value_per_unit, enchant.sale) == (None, None, "none")


def test_min_listings_ignores_thin_markets(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    store_snapshots(prices_conn, [PriceSnapshot(BOLT, NOW, market_value=130, quantity=2)],
                    source="test", realm=REALM, faction="")
    u = uses(conn, LINEN, depth=1, min_listings=5).uses[0]
    assert u.product_thin and u.product_listings == 2
    assert (u.sale, u.product_value, u.value_per_unit) == ("vendor", 25, 12)
    # Linen itself has no quantity recorded, so its AH price is trusted.
    r = uses(conn, LINEN, depth=1, min_listings=5)
    assert (r.thin, r.ah_sell) == (False, 48)
    assert uses(conn, LINEN, depth=1, min_listings=2).uses[0].sale == "market"


def test_api_uses(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 130)
    out = json.loads(json.dumps(api_uses(conn, {"item_id": str(LINEN), "realm": REALM, "depth": "1"})))
    assert out["best_sale"] == 48 and out["uses"][0]["value_per_unit"] == 62
    with pytest.raises(ApiError, match="unknown item"):
        api_uses(conn, {"item_id": "999999"})
    with pytest.raises(ApiError, match="depth"):
        api_uses(conn, {"item_id": str(LINEN), "depth": "4"})
    with pytest.raises(ApiError, match="item_id"):
        api_uses(conn, {})


TAILORING, ENCHANTING = 197, 333


def test_professions_filter_uses_and_later_steps(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 130)
    add(prices_conn, SHIRT, 1000)
    add(prices_conn, LESSER_ESS, 100)
    resolver = PriceResolver(conn, realm=REALM)
    # Without Tailoring, linen has no use left.
    r = find_uses(resolver, LINEN, professions={ENCHANTING})
    assert (r.uses, r.hidden, r.professions) == ([], 1, [ENCHANTING])
    # With it, the two-step use through the shirt is found, as without a filter.
    r = find_uses(resolver, LINEN, professions=[TAILORING, ENCHANTING])
    [u] = r.uses
    assert (u.sale, u.value_per_unit, r.hidden) == ("use", 235, 0)
    # An empty filter means every profession.
    r = find_uses(resolver, LINEN, professions=[])
    assert (len(r.uses), r.hidden, r.professions) == (1, 0, None)


def test_professions_filter_applies_to_later_steps(paths, conn, prices_conn):
    # Make the shirt (step 2) a Mining recipe, so a tailor-only filter stops after the bolt.
    with sqlite3.connect(paths["gamedata"]) as w:
        w.execute("UPDATE recipes SET profession_id = 186 WHERE spell_id = ?", (SHIRT_SPELL,))
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 130)
    add(prices_conn, SHIRT, 1000)
    resolver = PriceResolver(conn, realm=REALM)
    [u] = find_uses(resolver, LINEN, professions={TAILORING}).uses
    assert (u.sale, u.value_per_unit, u.then) == ("market", 62, None)
    [u] = find_uses(resolver, LINEN, professions={TAILORING, 186}).uses
    assert (u.sale, u.value_per_unit) == ("use", 235)


def test_api_uses_professions(conn, prices_conn):
    add(prices_conn, LINEN, 50)
    add(prices_conn, BOLT, 130)
    base = {"item_id": str(LINEN), "realm": REALM, "depth": "1"}
    out = json.loads(json.dumps(api_uses(conn, {**base, "professions": f"{TAILORING}, {ENCHANTING}"})))
    assert out["professions"] == [TAILORING, ENCHANTING]
    assert (len(out["uses"]), out["hidden"]) == (1, 0)
    out = api_uses(conn, {**base, "professions": str(ENCHANTING)})
    assert (out["uses"], out["hidden"]) == ([], 1)
    assert api_uses(conn, {**base, "professions": ""})["professions"] is None
    with pytest.raises(ApiError, match="unknown profession"):
        api_uses(conn, {**base, "professions": "754"})  # a racial, not a profession
    with pytest.raises(ApiError, match="profession id"):
        api_uses(conn, {**base, "professions": "tailoring"})


def test_api_meta_lists_professions_with_recipes(conn):
    profs = api_meta(conn)["professions"]
    # The SoD tailoring recipe is incomplete, so it isn't counted.
    assert [(p["id"], p["name"], p["recipes"]) for p in profs] == [
        (ENCHANTING, "Enchanting", 3), (186, "Mining", 1), (TAILORING, "Tailoring", 2),
    ]


def test_cli_profession_names(conn):
    from recipe_explorer.cli import _parse_professions

    assert _parse_professions(conn, None) is None
    assert _parse_professions(conn, ["tail", "Enchanting"]) == {TAILORING, ENCHANTING}
    with pytest.raises(SystemExit, match="matches none"):
        _parse_professions(conn, ["Alchemy"])
