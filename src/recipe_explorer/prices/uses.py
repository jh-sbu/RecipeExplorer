"""Forward from an item: what can it be made into, and what is one unit of it worth that way?

`chain.py` works backwards ("what does it take to make X?"). This module answers the
opposite question: "what is the most profitable way to use X?"

For a recipe R that consumes `c` units of X per cast and makes `n` units of product P:

    value of X via R = (realized value of P × n − cost of R's other reagents) ÷ c

* **Realized value of P** is the best of: selling it on the AH (after the cut), selling it
  to a vendor, or, while depth remains, using it in turn. The last option is recursive, and
  it is what finds multi-step uses (X → P → Q).
* **Other reagents** are valued at what they cost to obtain (the cheapest of AH, vendor or
  craft, from `PriceResolver.quote`), because you have to go and get them. This differs from
  the chain's value added, which values inputs at their AH price.

Every figure is copper per unit of X, so uses compare directly with the baselines: selling
X itself on the AH or to a vendor.

* **Thin markets:** with `min_listings`, an AH price only counts as a sale value when the
  snapshot saw at least that many listings. Snapshots with no quantity are trusted, since
  some sources don't report it.
* **Cycles:** recipes whose product is already on the path (essence up/down conversions)
  are skipped.
* **Recipes without an item product** (enchants) are listed, but can't be valued.
* **Professions:** with `professions` (profession ids), only recipes of those professions are
  considered, at every step. Other reagents are still costed as usual, which may include
  crafting them with a profession you don't have.
* **Skill:** `professions` may map a profession id to your skill in it. Then only recipes you
  can learn at that skill are considered. That's `recipes.learn_rank` where the client has it
  (recipes taught by an item, or that come with the profession). Trainer recipes have none,
  so they count from their yellow level: a cautious estimate, since trainers usually teach a
  recipe 10-25 points before it turns yellow.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field

from recipe_explorer.prices.resolver import MarketSnapshot, PriceResolver

MAX_DEPTH = 3


@dataclass
class Reagent:
    """One of a recipe's *other* reagents (not the item being used)."""

    item_id: int
    name: str
    count: float  # per cast
    unit_cost: int | None
    method: str  # how it's obtained: 'market' | 'vendor' | 'craft' | 'unknown'


@dataclass
class Use:
    spell_id: int
    recipe: str
    profession: str
    consumes: float  # units of the used item per cast
    product_item_id: int | None
    product_name: str | None
    product_quality: int | None
    product_count: float
    # Skill needed to learn the recipe; estimated (from its yellow level) for trainer recipes.
    learn_rank: int | None = None
    learn_rank_estimated: bool = False
    reagents: list[Reagent] = field(default_factory=list)
    other_cost: int | None = None  # per cast; None if a reagent can't be priced
    # The product's market, for judging liquidity.
    product_market: int | None = None  # AH price per unit, before the cut
    product_listings: int | None = None
    product_observed_at: str | None = None
    product_thin: bool = False  # AH price ignored: fewer listings than min_listings
    product_vendor_sell: int | None = None
    # How the product is best turned into money, and what that's worth per unit of it.
    sale: str = "none"  # 'market' | 'vendor' | 'use' | 'none'
    product_value: int | None = None
    then: Use | None = None  # the next step, when sale == 'use'
    value_per_cast: int | None = None
    value_per_unit: int | None = None  # per unit of the used item: the ranking figure

    @property
    def steps(self) -> list[Use]:
        """This use and every step after it."""
        out, u = [], self
        while u is not None:
            out.append(u)
            u = u.then
        return out


@dataclass
class UseReport:
    item_id: int
    name: str
    quality: int | None
    depth: int
    ah_cut: float
    min_listings: int
    # Baselines: selling the item as it is.
    market: int | None  # AH price per unit, before the cut
    listings: int | None
    observed_at: str | None
    thin: bool
    ah_sell: int | None  # per unit after the cut; None if no price or too thin
    vendor_sell: int | None
    uses: list[Use]
    # The profession filter, if any, your skill in those professions that have one, and how many
    # recipes that use the item directly it left out: of other professions, or above your skill.
    professions: list[int] | None = None
    skills: dict[int, int] | None = None
    hidden: int = 0
    hidden_skill: int = 0

    @property
    def best_sale(self) -> int | None:
        return max((v for v in (self.ah_sell, self.vendor_sell) if v is not None), default=None)

    def to_json(self) -> dict:
        d = asdict(self)
        d["best_sale"] = self.best_sale
        return d


def _rank(u: Use) -> tuple:
    # Valued uses first, best first; then unvalued ones that make an item; then the rest.
    return (u.value_per_unit is None, -(u.value_per_unit or 0), u.product_item_id is None, u.recipe)


Professions = Iterable[int] | Mapping[int, int | None]


class _Finder:
    def __init__(self, resolver: PriceResolver, min_listings: int, professions: Professions | None = None) -> None:
        self.r = resolver
        self.conn = resolver.conn
        self.min_listings = min_listings
        self.professions = frozenset(professions) if professions else None
        self.skills = {
            pid: skill for pid, skill in professions.items() if skill is not None
        } if isinstance(professions, Mapping) else {}
        self._items: dict[int, tuple[str, int | None, int | None]] = {}
        self._snap: dict[int, MarketSnapshot | None] = {}
        self._cost: dict[int, tuple[int | None, str]] = {}
        self._uses: dict[tuple, list[Use]] = {}

    # --- cached lookups -----------------------------------------------------------

    def item(self, item_id: int) -> tuple[str, int | None, int | None]:
        """(name, quality, vendor sell price or None)."""
        if item_id not in self._items:
            row = self.conn.execute("SELECT name, quality, sell_price FROM items WHERE id = ?", (item_id,)).fetchone()
            self._items[item_id] = (row[0], row[1], row[2] or None) if row else (f"Unknown item #{item_id}", None, None)
        return self._items[item_id]

    def snapshot(self, item_id: int) -> MarketSnapshot | None:
        if item_id not in self._snap:
            self._snap[item_id] = self.r.market_snapshot(item_id) if "market" in self.r.methods else None
        return self._snap[item_id]

    def thin(self, snap: MarketSnapshot | None) -> bool:
        return snap is not None and snap.quantity is not None and snap.quantity < self.min_listings

    def ah_sell(self, item_id: int) -> int | None:
        snap = self.snapshot(item_id)
        if snap is None or self.thin(snap):
            return None
        return round(snap.price * (1 - self.r.ah_cut))

    def cost(self, item_id: int) -> tuple[int | None, str]:
        if item_id not in self._cost:
            q = self.r.quote(item_id)
            self._cost[item_id] = (q.unit_cost, q.method)
        return self._cost[item_id]

    def consumers(self, item_id: int, filtered: bool = True) -> list:
        """Complete recipes that use `item_id`; with `filtered`, only those you can craft."""
        rows = self.all_consumers(item_id)
        return [r for r in rows if self.has_profession(r) and self.can_learn(r)] if filtered else rows

    def has_profession(self, rec) -> bool:
        return self.professions is None or rec["profession_id"] in self.professions

    def can_learn(self, rec) -> bool:
        skill = self.skills.get(rec["profession_id"])
        return skill is None or rec["learn_rank"] is None or rec["learn_rank"] <= skill

    def all_consumers(self, item_id: int) -> list:
        return self.conn.execute(
            """
            SELECT r.spell_id, r.name, p.name AS profession, r.profession_id,
                   r.product_item_id, coalesce(r.product_count, 1) AS product_count, rr.count AS consumes,
                   coalesce(r.learn_rank, r.skill_yellow) AS learn_rank,
                   r.learn_rank IS NULL AND r.skill_yellow IS NOT NULL AS learn_rank_estimated
              FROM recipe_reagents rr
              JOIN recipes r ON r.spell_id = rr.spell_id
              JOIN professions p ON p.id = r.profession_id
             WHERE rr.item_id = ? AND r.complete AND rr.count > 0
             ORDER BY r.spell_id
            """,
            (item_id,),
        ).fetchall()

    def other_reagents(self, spell_id: int, used_item: int) -> list:
        return self.conn.execute(
            "SELECT item_id, count FROM recipe_reagents WHERE spell_id = ? AND item_id != ? ORDER BY item_id",
            (spell_id, used_item),
        ).fetchall()

    # --- the search ---------------------------------------------------------------

    def uses(self, item_id: int, depth: int, path: frozenset[int]) -> list[Use]:
        """Every use of `item_id`, ranked. Products may be used further `depth - 1` times."""
        key = (item_id, depth, path)
        if key in self._uses:
            return self._uses[key]
        out = []
        for rec in self.consumers(item_id):
            product = rec["product_item_id"]
            if product is not None and product in path:
                continue  # leads back to an item already on the path
            reagents = []
            for rid, count in self.other_reagents(rec["spell_id"], item_id):
                unit, method = self.cost(rid)
                reagents.append(Reagent(rid, self.item(rid)[0], count, unit, method))
            other = None if any(g.unit_cost is None for g in reagents) else round(
                sum(g.unit_cost * g.count for g in reagents)
            )
            use = Use(
                spell_id=rec["spell_id"],
                recipe=rec["name"],
                profession=rec["profession"],
                consumes=rec["consumes"],
                product_item_id=product,
                product_name=None,
                product_quality=None,
                product_count=rec["product_count"],
                learn_rank=rec["learn_rank"],
                learn_rank_estimated=bool(rec["learn_rank_estimated"]),
                reagents=reagents,
                other_cost=other,
            )
            if product is not None:
                self._value_product(use, product, depth - 1, path | {product})
            out.append(use)
        out.sort(key=_rank)
        self._uses[key] = out
        return out

    def _value_product(self, use: Use, product: int, depth: int, path: frozenset[int]) -> None:
        name, quality, vendor_sell = self.item(product)
        snap = self.snapshot(product)
        use.product_name, use.product_quality, use.product_vendor_sell = name, quality, vendor_sell
        if snap is not None:
            use.product_market, use.product_listings, use.product_observed_at = (
                snap.price, snap.quantity, snap.observed_at,
            )
            use.product_thin = self.thin(snap)

        # Best way to realize the product. On a tie, selling beats doing more work.
        options: list[tuple[str, int, Use | None]] = []
        if (ah := self.ah_sell(product)) is not None:
            options.append(("market", ah, None))
        if vendor_sell is not None:
            options.append(("vendor", vendor_sell, None))
        if depth > 0:
            best = next((u for u in self.uses(product, depth, path) if u.value_per_unit is not None), None)
            if best is not None:
                options.append(("use", best.value_per_unit, best))
        if not options:
            return
        use.sale, use.product_value, use.then = max(options, key=lambda o: o[1])
        if use.other_cost is not None:
            use.value_per_cast = round(use.product_value * use.product_count - use.other_cost)
            use.value_per_unit = round(use.value_per_cast / use.consumes)


def find_uses(
    resolver: PriceResolver,
    item_id: int,
    depth: int = 2,
    min_listings: int = 0,
    professions: Professions | None = None,
) -> UseReport:
    """Rank the recipes that consume `item_id` by what they make one unit of it worth.

    `professions` (profession ids) limits uses, at every step, to those professions. None or
    empty means every profession. As a mapping of id to skill (or None for any skill), it also
    leaves out recipes you can't learn yet.
    """
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}")
    f = _Finder(resolver, min_listings, professions)
    direct = f.all_consumers(item_id)
    mine = [r for r in direct if f.has_profession(r)]
    hidden, hidden_skill = len(direct) - len(mine), sum(not f.can_learn(r) for r in mine)
    name, quality, vendor_sell = f.item(item_id)
    snap = f.snapshot(item_id)
    return UseReport(
        item_id=item_id,
        name=name,
        quality=quality,
        depth=depth,
        ah_cut=resolver.ah_cut,
        min_listings=min_listings,
        market=snap.price if snap else None,
        listings=snap.quantity if snap else None,
        observed_at=snap.observed_at if snap else None,
        thin=f.thin(snap),
        ah_sell=f.ah_sell(item_id),
        vendor_sell=vendor_sell,
        uses=f.uses(item_id, depth, frozenset({item_id})),
        professions=sorted(f.professions) if f.professions is not None else None,
        skills=dict(sorted(f.skills.items())) or None,
        hidden=hidden,
        hidden_skill=hidden_skill,
    )
