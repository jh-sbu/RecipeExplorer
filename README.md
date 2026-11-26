# Recipe Explorer (WoW Forever)

A local, offline recipe explorer for WoW Forever, built from the game client's own
DB2 tables (downloaded as CSV from [wago.tools](https://wago.tools), product `wowf`).
Auction house pricing plugs in through adapters; the source for that data hasn't been decided yet.

## Quick start

```sh
uv sync --extra ui          # drop --extra ui if you don't want Datasette
uv run rex build            # fetch the latest Forever build + build data/gamedata.sqlite
uv run rex info             # build number, recipe counts per profession

uv run rex recipes -p tailoring --skill 150      # learnable and still giving skill-ups at 150
uv run rex recipes -s "silk" -v                  # search, with reagents + recipe items
uv run rex uses "Bolt of Silk Cloth"             # what consumes this reagent
uv run rex serve --open                          # browse everything in Datasette
```

With price data:

```sh
uv run rex prices import csv my_prices.csv --realm MyRealm [--faction horde]
uv run rex cost "Bolt of Silk Cloth" --realm MyRealm      # cheapest of AH / vendor / craft, as a tree
uv run rex profit tailoring --realm MyRealm --max-age-days 3
uv run rex uses "Silk Cloth" --value --realm MyRealm      # most profitable things to turn it into
uv run rex uses "Silk Cloth" --value --realm MyRealm -p tailoring:150 -p first   # ...with your professions
```

### Crafting chain graph

```sh
pnpm --dir web install && pnpm --dir web build   # once, and after frontend changes (needs Node)
uv run rex ui --open                              # http://127.0.0.1:8002
```

Add items to make. The planner lays out everything they need as a left-to-right graph:
raw materials on the left, targets on the right, with quantities summed when several
steps share an intermediate. Each node has a dropdown: *Auto* (cheapest), a specific
recipe, or buy on the AH / from a vendor. Targets are crafted by default.

Each crafting step shows its **value added**: the AH value of its output, minus the value
of its inputs. Inputs are valued at their AH price, or at their cost (marked `*`, dashed
edge) if they have none. Across a chain where everything has AH prices, the steps sum to
(final product on the AH) − (raw materials on the AH), so you can see which step actually
makes the money. It can be shown as a total, per item, or as a % over inputs.

### Most profitable use of an item

Select a node and open the **Use** tab in the details panel (or click a node's *uses ⇢*
button). It answers "what is the most profitable way to use this item?" by ranking every
recipe that consumes it by **value per unit of the item**:

```
(product's realized value × products per cast − cost of the other reagents) ÷ units of the item per cast
```

* The product's **realized value** is the best of selling it on the AH (after the cut),
  selling it to a vendor, or using it in a further recipe. Following those further uses
  gives multi-step paths (X → Y → Z). *Steps to follow* sets how deep to look (1–3, default 2).
* **Other reagents** are valued at what they cost to obtain (cheapest of AH, vendor or
  craft). The graph's value added values inputs at their AH price instead.
* The baselines are selling the item as it is, on the AH or to a vendor. Each use shows its gain over the better of the two.
* **Min listings** ignores AH prices seen with fewer listings than that, so a single
  overpriced listing can't top the table. Each row shows the product's listing count and age.
* Recipes that loop back to an item already on the path (essence conversions) are skipped.
  Recipes that make no item (enchants) are listed separately, without a value.
* **My professions** (sidebar) limits uses, and the steps they continue into, to recipes of
  the professions you tick. Uses of other professions are hidden, and the tab says how many.
  Nothing ticked means every profession. The other reagents are still costed the cheapest
  way, which may be crafting them with a profession you don't have. Give a **skill** for a
  ticked profession to also hide its recipes you can't learn yet. The tab counts those too,
  and each use shows the skill it needs. Trainer recipes have no learn rank in the data (see
  below), so they count from their yellow level, shown as `~270`. You can usually learn them
  10–25 points sooner. The setting is saved with the plan. On the CLI, use `-p NAME` or
  `-p NAME:SKILL` (name or prefix, repeatable).

Expand a row to see the arithmetic for each step. *Plan this* adds the final product as
a target, sized to use the selected node's quantity, and sets each step to craft with
that path's recipes, so the graph shows the whole chain.

Plans (targets, choices, node positions) persist in the browser and can be exported and
imported as JSON. For frontend work, run `pnpm --dir web dev` (hot reload on :5173, with
`/api` proxied to a running `rex ui`).

When a new client patch lands, run `uv run rex build` again. Raw CSVs are kept per build
under `data/raw/<build>/`, so you can rebuild or diff any old build offline.

## Architecture

```
 wago.tools CSV ──fetch──▶ data/raw/<build>/*.csv ──normalize──▶ data/gamedata.sqlite   (rebuilt each patch)
                                        manual/*.csv ─────────────┘
 price sources ──adapter──▶ PriceSnapshot ──store──▶ data/prices.sqlite                  (kept forever)

 gamedata.sqlite + ATTACH prices.sqlite ──▶ PriceResolver ──▶ CLI / Datasette
                                                   ├──▶ build_chain ──▶ rex ui (JSON API) ──▶ web/ (React Flow)
                                                   └──▶ find_uses ───┘
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
* **`prices/chain.py` turns that into a graph** for the UI: one node per item (one decision
  each), demand pushed down from the targets, and value added per step. Crafting cycles
  (e.g. essence conversions) get a separate "bought" copy of the item, so the graph stays acyclic.
* **`prices/uses.py` goes the other way:** from an item to what it can become. It is a
  depth-limited search over `recipe_reagents`, memoized per (item, depth, path).
* **The UI server is standard library only** (`ui/server.py`, `http.server`). The frontend in
  `web/` (Vite, React, React Flow, ELK for layout) builds into `src/recipe_explorer/ui/static/`.

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
* **Learn rank.** `min_skill` is `SkillLineAbility.MinSkillLineRank`, which is 1 for nearly every
  recipe in the Forever client, so it can't tell you when a recipe is learned. `learn_rank`
  comes from the item that teaches the recipe (its `RequiredSkillRank`; the lowest if several
  items teach it), e.g. Mooncloth at 230 from *Pattern: Mooncloth*. Recipes that come with
  the profession (`acquire_method` 1) get 1. That covers about three quarters of recipes.
  Trainer-taught recipes have no learn rank, because trainer lists are server data. The skill
  filters use their yellow level instead. `skill_yellow`/`skill_grey` come straight from the client and look
  right. `skill_green` is the usual midpoint.
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
