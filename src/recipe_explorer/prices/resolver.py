"""Answer "what does it cost to obtain item X?" from whatever price data exists.

The resolver is the only thing the rest of the app should ask for prices. It picks
the cheapest of:

  * market  - latest AH snapshot for the realm/faction (optionally age-limited)
  * vendor  - vendor buy price, only for items listed in vendor_items
  * craft   - sum of reagent costs (resolved recursively) / items produced

so adding a new price source never requires touching callers.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

METHODS = ("market", "vendor", "craft")


@dataclass
class Quote:
    item_id: int
    name: str
    quantity: float
    unit_cost: int | None  # copper; None if no way to price it
    method: str  # one of METHODS, or 'unknown'
    recipe_spell_id: int | None = None
    components: list[Quote] = field(default_factory=list)
    options: dict[str, int] = field(default_factory=dict)  # every method that produced a price

    @property
    def total(self) -> int | None:
        return None if self.unit_cost is None else round(self.unit_cost * self.quantity)


@dataclass
class CraftCost:
    """Reagent cost of casting a recipe `crafts` times."""

    spell_id: int
    name: str
    crafts: float
    product_item_id: int | None
    items_produced: float
    components: list[Quote]
    total: int | None  # copper for all crafts; None if any reagent is unpriceable

    @property
    def per_item(self) -> int | None:
        if self.total is None or not self.items_produced:
            return None
        return round(self.total / self.items_produced)


@dataclass
class Profit:
    spell_id: int
    recipe: str
    product_count: float
    revenue: int | None  # per cast, after AH cut
    cost: CraftCost
    profit: int | None


class PriceResolver:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        realm: str | None = None,
        faction: str = "",
        max_age: timedelta | None = None,
        methods: tuple[str, ...] = METHODS,
        ah_cut: float = 0.05,
    ) -> None:
        """`conn` must be a gamedata connection with the prices DB attached (see db.open_gamedata).

        With realm=None, market prices are ignored entirely.
        """
        self.conn = conn
        self.realm = realm
        self.faction = faction.lower()
        self.max_age = max_age
        self.methods = methods
        self.ah_cut = ah_cut

    # --- single-method lookups ------------------------------------------------

    def market_price(self, item_id: int) -> int | None:
        if self.realm is None:
            return None
        sql = """
            SELECT coalesce(market_value, min_buyout) AS price
              FROM prices.price_snapshots
             WHERE item_id = ? AND realm = ? AND faction IN (?, '')
               AND coalesce(market_value, min_buyout) IS NOT NULL
        """
        params: list = [item_id, self.realm, self.faction]
        if self.max_age is not None:
            sql += " AND observed_at >= ?"
            params.append((datetime.now(UTC) - self.max_age).isoformat(timespec="seconds"))
        # Prefer the exact faction over shared ('') data, then the newest observation.
        sql += " ORDER BY (faction = ?) DESC, observed_at DESC LIMIT 1"
        params.append(self.faction)
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def vendor_price(self, item_id: int) -> int | None:
        row = self.conn.execute(
            """
            SELECT i.buy_price, i.vendor_stack_count
              FROM vendor_items v JOIN items i ON i.id = v.item_id
             WHERE v.item_id = ?
            """,
            (item_id,),
        ).fetchone()
        if not row or not row[0]:
            return None
        # BuyPrice is for a purchase of vendor_stack_count units.
        return round(row[0] / max(row[1] or 1, 1))

    # --- composite ------------------------------------------------------------

    def quote(self, item_id: int, quantity: float = 1) -> Quote:
        return self._quote(item_id, quantity, frozenset())

    def recipe_cost(self, spell_id: int, crafts: float = 1) -> CraftCost:
        """Cost of the reagents for `crafts` casts of a recipe."""
        return self._recipe_cost(spell_id, crafts, frozenset())

    def profit(self, spell_id: int) -> Profit:
        """Per-cast profit: AH value of the product (after cut) minus cheapest reagent cost."""
        rec = self.conn.execute(
            "SELECT name, product_item_id, product_count FROM recipes WHERE spell_id = ?",
            (spell_id,),
        ).fetchone()
        if rec is None:
            raise KeyError(f"No recipe with spell_id {spell_id}")
        cost = self.recipe_cost(spell_id)
        revenue = None
        if rec["product_item_id"] is not None:
            unit = self.market_price(rec["product_item_id"])
            if unit is not None:
                revenue = round(unit * rec["product_count"] * (1 - self.ah_cut))
        profit = None if revenue is None or cost.total is None else revenue - cost.total
        return Profit(spell_id, rec["name"], rec["product_count"] or 0, revenue, cost, profit)

    # --- internals ------------------------------------------------------------

    def _item_name(self, item_id: int) -> str:
        row = self.conn.execute("SELECT name FROM items WHERE id = ?", (item_id,)).fetchone()
        return row[0] if row else f"Unknown item #{item_id}"

    def _quote(self, item_id: int, quantity: float, visiting: frozenset[int]) -> Quote:
        name = self._item_name(item_id)
        options: dict[str, int] = {}
        best_craft: CraftCost | None = None

        if "market" in self.methods and (p := self.market_price(item_id)) is not None:
            options["market"] = p
        if "vendor" in self.methods and (p := self.vendor_price(item_id)) is not None:
            options["vendor"] = p
        # `visiting` breaks cycles such as essence <-> greater essence conversions.
        if "craft" in self.methods and item_id not in visiting:
            for (spell_id,) in self.conn.execute(
                "SELECT spell_id FROM recipes WHERE product_item_id = ?", (item_id,)
            ).fetchall():
                cost = self._recipe_cost(spell_id, 1, visiting | {item_id})
                if cost.per_item is None:
                    continue
                if best_craft is None or cost.per_item < options["craft"]:
                    best_craft, options["craft"] = cost, cost.per_item

        if not options:
            return Quote(item_id, name, quantity, None, "unknown")
        method = min(options, key=options.__getitem__)
        quote = Quote(item_id, name, quantity, options[method], method, options=options)
        if method == "craft" and best_craft is not None:
            crafts = quantity / best_craft.items_produced
            quote.recipe_spell_id = best_craft.spell_id
            quote.components = [
                self._quote(c.item_id, c.quantity * crafts, visiting | {item_id})
                for c in best_craft.components
            ]
        return quote

    def _recipe_cost(self, spell_id: int, crafts: float, visiting: frozenset[int]) -> CraftCost:
        rec = self.conn.execute(
            "SELECT name, product_item_id, coalesce(product_count, 1) FROM recipes WHERE spell_id = ?",
            (spell_id,),
        ).fetchone()
        if rec is None:
            raise KeyError(f"No recipe with spell_id {spell_id}")
        components = [
            self._quote(item_id, count * crafts, visiting)
            for item_id, count in self.conn.execute(
                "SELECT item_id, count FROM recipe_reagents WHERE spell_id = ?", (spell_id,)
            ).fetchall()
        ]
        totals = [c.total for c in components]
        return CraftCost(
            spell_id=spell_id,
            name=rec[0],
            crafts=crafts,
            product_item_id=rec[1],
            items_produced=rec[2] * crafts,
            components=components,
            total=None if any(t is None for t in totals) else sum(totals),
        )
