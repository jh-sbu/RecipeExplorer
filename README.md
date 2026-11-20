# Recipe Explorer (WoW Forever)

A local, offline recipe explorer for WoW Forever, built from the game client's own
DB2 tables (downloaded as CSV from [wago.tools](https://wago.tools), product `wowf`).
Auction house pricing plugs in through adapters; the source for that data hasn't been decided yet.

## Quick start

```sh
uv sync --extra ui          # drop --extra ui if you don't want Datasette
uv run rex build            # fetch the latest Forever build + build data/gamedata.sqlite
uv run rex info             # build number, recipe counts per profession

uv run rex recipes -p tailoring --skill 150      # what still gives skill-ups at 150
uv run rex recipes -s "silk" -v                  # search, with reagents + recipe items
uv run rex uses "Bolt of Silk Cloth"             # what consumes this reagent
uv run rex serve --open                          # browse everything in Datasette
```

With price data:

```sh
uv run rex prices import csv my_prices.csv --realm MyRealm [--faction horde]
uv run rex cost "Bolt of Silk Cloth" --realm MyRealm      # cheapest of AH / vendor / craft, as a tree
uv run rex profit tailoring --realm MyRealm --max-age-days 3
```

When a new client patch lands, run `uv run rex build` again. Raw CSVs are kept per build
under `data/raw/<build>/`, so you can rebuild or diff any old build offline.

## Architecture

```
 wago.tools CSV ──fetch──▶ data/raw/<build>/*.csv ──normalize──▶ data/gamedata.sqlite   (rebuilt each patch)
                                        manual/*.csv ─────────────┘
 price sources ──adapter──▶ PriceSnapshot ──store──▶ data/prices.sqlite                  (kept forever)

 gamedata.sqlite + ATTACH prices.sqlite ──▶ PriceResolver ──▶ CLI / Datasette / future UI
```

* **Two databases on purpose.** Game data is disposable and regenerated per build; price
  history is not. Rebuilding never touches `prices.sqlite`. Item IDs are stable across
  builds, so prices join to game data by `item_id`, without foreign keys.
* **All raw DB2 knowledge lives in `ingest/normalize.py`.** It checks the columns it needs up
  front (`REQUIRED_COLUMNS`). If a new build renames a column, the build fails loudly and
  the previous `gamedata.sqlite` stays in place, because the new DB is written to a temp
  file and swapped in.
* **`PriceResolver` is the only thing that knows about prices.** It returns the cheapest of:
  * **market:** the latest AH snapshot for the realm and faction. Faction-specific data
    wins over shared data. An optional max age applies.
  * **vendor:** the client buy price. It's only used for items listed in `manual/vendor_items.csv`.
  * **craft:** reagent costs resolved recursively, divided by items produced. Cycles
    (e.g. essence conversions) are cut off.

  It also computes per-cast profit after the AH cut (default 5%).

### Schema (gamedata)

| table | contents |
|---|---|
| `professions` | crafting skill lines (primary/secondary) |
| `recipes` | one row per recipe spell: skill thresholds, product item + count, `complete` flag |
| `recipe_reagents` | reagents per recipe |
| `recipe_teaches` | recipe/pattern/plan item → spell it teaches |
| `items` | names, quality, levels, vendor buy/sell prices, `in_client` flag |
| `vendor_items`, `recipe_sources` | hand-maintained, from `manual/*.csv` |
| `v_recipes`, `v_reagent_usage` | readable views used by the CLI and Datasette |

## Things the client data does *not* tell you

* **Where a recipe comes from** (trainer, vendor, drop, quest). That's server-side.
  `manual/recipe_sources.csv` exists for this and is empty for now.
* **Which items vendors sell.** `BuyPrice` is set on almost every item, so the resolver only
  trusts it for items listed in `manual/vendor_items.csv`. The per-unit price is
  `BuyPrice / VendorStackCount`. That matches known prices like Crystal Vial at 5s, but spot-check
  others in-game.
* **Learn rank.** `min_skill` is `SkillLineAbility.MinSkillLineRank`, which doesn't always match
  the rank you can learn the recipe at. `skill_yellow`/`skill_grey` come straight from the
  client. `skill_green` is the usual midpoint.
* **Leftover content.** The Forever client still contains recipes whose items have no
  `ItemSparse` row. Most look like Season of Discovery or removed content. Those recipes get
  `complete = 0` and are hidden by default (`--all` shows them). Some leftover recipes may
  still pass this check.

Manual CSVs are checked against each build. Unknown IDs or name mismatches print a warning
and are skipped.

## Adding a price source

1. Write a class in `src/recipe_explorer/prices/adapters/` with a `name` ClassVar, an
   `__init__(self, path)`, and `read() -> Iterator[PriceSnapshot]`. Money values are copper per unit.
2. Register it in `ADAPTERS` in `prices/adapters/__init__.py`.
3. `uv run rex prices import <name> <path> --realm ...`

Re-importing the same scan is a no-op: snapshots are unique per source, realm, faction,
time, and item. The built-in `csv` adapter (`item_id, min_buyout, market_value, quantity,
observed_at`) works for hand-entered prices or as a target format for other tools.

## Development

```sh
uv run pytest
```

Tests build a tiny synthetic dataset (`tests/conftest.py`). It's written using the
normalizer's own `REQUIRED_COLUMNS`, so the fixtures can't drift from what the code reads.
Set `REX_DATA_DIR` to point the CLI at a different data directory.
