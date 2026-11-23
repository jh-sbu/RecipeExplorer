"""Crafting chains as a graph, with the value each crafting step adds.

`PriceResolver.quote` returns a *tree*, so an intermediate used in two places shows up
twice. A planner view wants a *graph*: one node per item, with quantities summed. This
module builds that graph.

Each item gets one decision, the cheapest way to obtain it (or the user's override).
Targets are crafted by default when they have a recipe, even if buying them is cheaper:

  * a **step** node: the item is crafted from a recipe. Its inputs are edges from other nodes.
  * a **source** node: the item is bought (market / vendor), or can't be priced (unknown).

**Value added** (per step) = what the step's output is worth on the AH, minus what its
inputs are worth. An input is valued at its AH price. If there is none (e.g. vendor
thread), it falls back to whatever the input costs to obtain. That way, across a chain
where everything has AH prices, the steps' value added sums to
(final product's AH value) - (raw materials' AH value).

Cycles (essence conversions, etc.): while an item is being decided, a recipe that needs
that same item again sees a *bought* copy of it. That copy is its own source node
(id ``"<item>:buy"``), so the graph is always acyclic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field

from recipe_explorer.prices.resolver import PriceResolver

BUY_METHODS = ("market", "vendor")


@dataclass(frozen=True)
class Override:
    """User decision for one item: how to obtain it. `recipe` only applies to method='craft'."""

    method: str  # 'market' | 'vendor' | 'craft'
    recipe: int | None = None


@dataclass
class RecipeOption:
    spell_id: int
    name: str
    profession: str
    product_count: float
    unit_cost: int | None  # per item produced, with the graph's decisions for its inputs


@dataclass
class Edge:
    source: str  # node id of the input
    target: str  # node id of the step that consumes it
    item_id: int
    quantity: float = 0  # units of the input flowing along this edge
    per_craft: float = 0  # reagent count per cast
    ref_unit: int | None = None  # value of one unit for value-added purposes
    ref_basis: str = "unknown"  # 'market' | the input's own method if no AH price

    @property
    def ref_total(self) -> int | None:
        return None if self.ref_unit is None else round(self.ref_unit * self.quantity)


@dataclass
class Node:
    id: str
    item_id: int
    name: str
    quality: int | None
    kind: str  # 'step' | 'source'
    method: str  # 'craft' | 'market' | 'vendor' | 'unknown'
    options: dict[str, int] = field(default_factory=dict)  # every method that produced a price
    unit_cost: int | None = None  # cheapest/overridden cost per unit
    market: int | None = None  # AH price per unit, if known
    quantity: float = 0
    is_target: bool = False
    overridden: bool = False
    cycle_copy: bool = False  # bought copy created to break a crafting cycle
    # step-only
    recipe: RecipeOption | None = None
    recipes: list[RecipeOption] = field(default_factory=list)  # all recipes producing the item
    crafts: float = 0
    note: str | None = None

    @property
    def total_cost(self) -> int | None:
        return None if self.unit_cost is None else round(self.unit_cost * self.quantity)


@dataclass
class ChainGraph:
    nodes: list[Node]
    edges: list[Edge]
    targets: list[str]  # node ids, in request order

    def node(self, node_id: str) -> Node:
        return next(n for n in self.nodes if n.id == node_id)

    def value_added(self, node: Node) -> dict:
        """1b: AH value of the step's output minus its inputs' reference value."""
        if node.kind != "step":
            return {"output_value": None, "input_value": None, "value_added": None, "complete": True}
        inputs = [e for e in self.edges if e.target == node.id]
        input_value = None if any(e.ref_unit is None for e in inputs) else sum(e.ref_total for e in inputs)
        output_value = None if node.market is None else round(node.market * node.quantity)
        added = None if input_value is None or output_value is None else output_value - input_value
        return {
            "output_value": output_value,
            "input_value": input_value,
            "value_added": added,
            "value_added_pct": None if added is None or not input_value else added / input_value,
            # False when some input had to be valued at its cost instead of an AH price.
            "complete": all(e.ref_basis == "market" for e in inputs),
        }

    def summary(self, ah_cut: float) -> dict:
        sources = [n for n in self.nodes if n.kind == "source"]
        raw_cost = None if any(n.total_cost is None for n in sources) else sum(n.total_cost for n in sources)
        targets = [self.node(t) for t in self.targets]
        target_cost = None if any(n.total_cost is None for n in targets) else sum(n.total_cost for n in targets)
        target_market = (
            None if any(n.market is None for n in targets) else sum(round(n.market * n.quantity) for n in targets)
        )
        revenue = None if target_market is None else round(target_market * (1 - ah_cut))
        steps = [self.value_added(n) for n in self.nodes if n.kind == "step"]
        known = [s["value_added"] for s in steps if s["value_added"] is not None]
        return {
            "raw_cost": raw_cost,  # what everything bought costs
            "target_cost": target_cost,  # cost of the targets as planned
            "target_market": target_market,
            "revenue_after_cut": revenue,
            "profit": None if revenue is None or target_cost is None else revenue - target_cost,
            "value_added_known": sum(known),
            "steps": len(steps),
            "steps_unvalued": len(steps) - len(known),
        }

    def to_json(self, ah_cut: float = 0.05) -> dict:
        nodes = []
        for n in self.nodes:
            d = asdict(n)
            d["total_cost"] = n.total_cost
            d.update(self.value_added(n))
            nodes.append(d)
        edges = []
        for e in self.edges:
            d = asdict(e)
            d["id"] = f"{e.source}->{e.target}"
            d["ref_total"] = e.ref_total
            edges.append(d)
        return {"nodes": nodes, "edges": edges, "targets": self.targets, "summary": self.summary(ah_cut)}


class _Planner:
    def __init__(
        self, resolver: PriceResolver, overrides: Mapping[int, Override], prefer_craft: set[int] = frozenset()
    ) -> None:
        self.r = resolver
        self.prefer_craft = prefer_craft  # targets: crafted by default, since that's what the plan is for
        self.conn = resolver.conn
        self.overrides = overrides
        self.methods = resolver.methods
        self.decided: dict[int, Node] = {}  # item_id -> its one decision node
        self.inputs: dict[str, list[tuple[str, int, int]]] = {}  # step id -> [(input node id, item, per craft)]
        self.copies: dict[str, Node] = {}  # cycle-breaking bought copies
        self.in_progress: set[int] = set()
        self._market: dict[int, int | None] = {}
        self._vendor: dict[int, int | None] = {}

    # --- cached lookups -------------------------------------------------------

    def market(self, item_id: int) -> int | None:
        if item_id not in self._market:
            self._market[item_id] = self.r.market_price(item_id) if "market" in self.methods else None
        return self._market[item_id]

    def vendor(self, item_id: int) -> int | None:
        if item_id not in self._vendor:
            self._vendor[item_id] = self.r.vendor_price(item_id) if "vendor" in self.methods else None
        return self._vendor[item_id]

    def item_row(self, item_id: int):
        return self.conn.execute("SELECT name, quality FROM items WHERE id = ?", (item_id,)).fetchone()

    def recipes_for(self, item_id: int) -> list:
        return self.conn.execute(
            """
            SELECT r.spell_id, r.name, p.name AS profession, coalesce(r.product_count, 1) AS product_count
              FROM recipes r JOIN professions p ON p.id = r.profession_id
             WHERE r.product_item_id = ? AND r.complete
             ORDER BY r.spell_id
            """,
            (item_id,),
        ).fetchall()

    def reagents(self, spell_id: int) -> list:
        return self.conn.execute(
            "SELECT item_id, count FROM recipe_reagents WHERE spell_id = ? ORDER BY item_id", (spell_id,)
        ).fetchall()

    # --- decisions ------------------------------------------------------------

    def _base_node(self, item_id: int, node_id: str) -> Node:
        row = self.item_row(item_id)
        node = Node(
            id=node_id,
            item_id=item_id,
            name=row["name"] if row else f"Unknown item #{item_id}",
            quality=row["quality"] if row else None,
            kind="source",
            method="unknown",
            market=self.market(item_id),
        )
        for m, price in (("market", node.market), ("vendor", self.vendor(item_id))):
            if price is not None:
                node.options[m] = price
        return node

    def _buy(self, node: Node, forced: str | None = None) -> Node:
        """Turn `node` into a source using the cheapest buy option (or `forced`)."""
        buy = {m: p for m, p in node.options.items() if m in BUY_METHODS}
        node.kind = "source"
        if forced is not None:
            node.method, node.unit_cost = forced, buy.get(forced)
            if forced not in buy:
                node.note = f"no {forced} price"
        elif buy:
            node.method = min(buy, key=buy.__getitem__)
            node.unit_cost = buy[node.method]
        else:
            node.method, node.unit_cost = "unknown", None
        return node

    def cycle_copy(self, item_id: int) -> str:
        node_id = f"{item_id}:buy"
        if node_id not in self.copies:
            node = self._buy(self._base_node(item_id, node_id))
            node.cycle_copy = True
            node.note = "bought: crafting it here would loop back on itself"
            self.copies[node_id] = node
        return node_id

    def decide(self, item_id: int) -> Node:
        if item_id in self.decided:
            return self.decided[item_id]
        self.in_progress.add(item_id)
        node = self._base_node(item_id, str(item_id))
        override = self.overrides.get(item_id)
        node.overridden = override is not None

        # Price every recipe. Inputs that are mid-decision (a cycle) are bought copies.
        candidates: list[tuple[RecipeOption, list[tuple[str, int, int]]]] = []
        for rec in self.recipes_for(item_id):
            refs, total = [], 0
            for reagent_id, count in self.reagents(rec["spell_id"]):
                if reagent_id in self.in_progress:
                    ref = self.cycle_copy(reagent_id)
                    cost = self.copies[ref].unit_cost
                else:
                    child = self.decide(reagent_id)
                    ref, cost = child.id, child.unit_cost
                refs.append((ref, reagent_id, count))
                total = None if total is None or cost is None else total + cost * count
            per_item = None if total is None or not rec["product_count"] else round(total / rec["product_count"])
            opt = RecipeOption(rec["spell_id"], rec["name"], rec["profession"], rec["product_count"], per_item)
            candidates.append((opt, refs))
        node.recipes = [c[0] for c in candidates]

        priced = [c for c in candidates if c[0].unit_cost is not None]
        if "craft" in self.methods and priced:
            node.options["craft"] = min(c[0].unit_cost for c in priced)

        choice = None
        if override is not None and override.method == "craft":
            pool = [c for c in candidates if override.recipe is None or c[0].spell_id == override.recipe]
            if pool:
                # Cheapest priced recipe in the pool, else the first one (shows as unpriceable).
                choice = min(pool, key=lambda c: (c[0].unit_cost is None, c[0].unit_cost or 0))
            else:
                node.note = "no such recipe for this item; using cheapest option"
        elif override is not None:
            self._buy(node, override.method)
        else:
            best_buy = min((p for m, p in node.options.items() if m in BUY_METHODS), default=None)
            craft = node.options.get("craft")
            if candidates and item_id in self.prefer_craft and "craft" in self.methods:
                choice = min(priced, key=lambda c: c[0].unit_cost) if priced else candidates[0]
            elif craft is not None and (best_buy is None or craft < best_buy):
                choice = min(priced, key=lambda c: c[0].unit_cost)
            elif best_buy is None and candidates and "craft" in self.methods and not priced:
                # Nothing is priceable, but it *is* craftable: show the chain anyway.
                choice = candidates[0]

        if choice is not None:
            node.kind, node.method = "step", "craft"
            node.recipe, node.unit_cost = choice[0], choice[0].unit_cost
            self.inputs[node.id] = choice[1]
        elif override is None:
            self._buy(node)

        self.in_progress.discard(item_id)
        self.decided[item_id] = node
        return node


def build_chain(
    resolver: PriceResolver,
    targets: Iterable[tuple[int, float]],
    overrides: Mapping[int, Override] | None = None,
) -> ChainGraph:
    """Plan how to make `targets` [(item_id, quantity)], as a merged, acyclic graph."""
    targets = list(targets)
    planner = _Planner(resolver, overrides or {}, {item_id for item_id, _ in targets})
    target_ids: list[str] = []
    for item_id, _ in targets:
        node = planner.decide(item_id)
        if node.id not in target_ids:
            target_ids.append(node.id)

    # Keep only what the targets actually reach, and order it so consumers come first.
    all_nodes = {n.id: n for n in [*planner.decided.values(), *planner.copies.values()]}
    order: list[str] = []
    seen: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        for child_id, _, _ in planner.inputs.get(node_id, []):
            visit(child_id)
        order.append(node_id)  # post-order: inputs before consumers

    for t in target_ids:
        visit(t)
    order.reverse()  # consumers first

    # Push demand down from the targets.
    for item_id, qty in targets:
        all_nodes[str(item_id)].quantity += qty
        all_nodes[str(item_id)].is_target = True
    edges: dict[tuple[str, str], Edge] = {}
    for node_id in order:
        node = all_nodes[node_id]
        if node.kind != "step" or node.recipe is None:
            continue
        node.crafts = node.quantity / node.recipe.product_count if node.recipe.product_count else 0
        for child_id, reagent_id, count in planner.inputs[node_id]:
            child = all_nodes[child_id]
            qty = count * node.crafts
            child.quantity += qty
            edge = edges.setdefault(
                (child_id, node_id), Edge(child_id, node_id, reagent_id, per_craft=count)
            )
            edge.quantity += qty

    for edge in edges.values():
        child = all_nodes[edge.source]
        if child.market is not None:
            edge.ref_unit, edge.ref_basis = child.market, "market"
        else:
            edge.ref_unit, edge.ref_basis = child.unit_cost, child.method

    return ChainGraph(nodes=[all_nodes[i] for i in order], edges=list(edges.values()), targets=target_ids)
