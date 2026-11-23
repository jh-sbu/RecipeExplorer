"""Command-line entry point: `rex <command>`."""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from recipe_explorer import config
from recipe_explorer.db import open_gamedata, open_prices
from recipe_explorer.prices import CraftCost, PriceResolver, Quote, store_snapshots
from recipe_explorer.prices.adapters import ADAPTERS


def fmt_money(copper: float | None) -> str:
    if copper is None:
        return "?"
    copper = round(copper)
    sign = "-" if copper < 0 else ""
    g, rem = divmod(abs(copper), 10000)
    s, c = divmod(rem, 100)
    parts = [f"{g}g"] if g else []
    if s or g:
        parts.append(f"{s}s")
    parts.append(f"{c}c")
    return sign + " ".join(parts)


def _fmt_qty(q: float) -> str:
    return f"{q:g}"


def resolve_item(conn, ref: str) -> int:
    if ref.isdigit():
        return int(ref)
    rows = conn.execute("SELECT id, name FROM items WHERE name = ? COLLATE NOCASE", (ref,)).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT id, name FROM items WHERE name LIKE ? ORDER BY length(name) LIMIT 10", (f"%{ref}%",)
        ).fetchall()
    if not rows:
        sys.exit(f"No item matching {ref!r}")
    if len(rows) > 1 and rows[0]["name"].lower() != ref.lower():
        listing = "\n".join(f"  {r['id']:>6}  {r['name']}" for r in rows)
        sys.exit(f"Ambiguous item {ref!r}; use an ID:\n{listing}")
    return rows[0]["id"]


def _make_resolver(conn, args) -> PriceResolver:
    return PriceResolver(
        conn,
        realm=args.realm,
        faction=args.faction,
        max_age=timedelta(days=args.max_age_days) if args.max_age_days else None,
    )


# --- commands ------------------------------------------------------------------


def cmd_builds(args) -> None:
    from recipe_explorer.ingest.wago import list_builds

    for b in list_builds()[: args.limit]:
        print(b)


def _pick_build(build: str | None) -> str:
    if build and build != "latest":
        return build
    from recipe_explorer.ingest.wago import latest_build

    return latest_build()


def cmd_fetch(args) -> None:
    from recipe_explorer.ingest.wago import fetch_tables

    build = _pick_build(args.build)
    print(f"Fetching Forever build {build}")
    print(fetch_tables(build, force=args.force))


def cmd_build(args) -> None:
    from recipe_explorer.ingest.normalize import build_gamedata
    from recipe_explorer.ingest.wago import fetch_tables

    build = _pick_build(args.build)
    raw = fetch_tables(build)
    print(f"Normalizing {raw} -> {config.GAMEDATA_DB}")
    stats = build_gamedata(raw, config.GAMEDATA_DB, build=build)
    for k, v in stats.items():
        print(f"  {k:16} {v}")


def cmd_info(args) -> None:
    conn = open_gamedata()
    for row in conn.execute("SELECT key, value FROM meta ORDER BY key"):
        print(f"{row['key']:10} {row['value']}")
    print()
    for row in conn.execute(
        "SELECT p.name, p.category, count(*) n FROM recipes r JOIN professions p ON p.id = r.profession_id "
        "GROUP BY p.id ORDER BY p.category, p.name"
    ):
        print(f"  {row['name']:16} {row['category']:9} {row['n']:>5} recipes")
    n = conn.execute("SELECT count(*), count(DISTINCT item_id), max(observed_at) FROM prices.price_snapshots").fetchone()
    print(f"\nprices: {n[0]} snapshots for {n[1]} items, newest {n[2] or '-'}")


def cmd_recipes(args) -> None:
    conn = open_gamedata()
    sql = "SELECT * FROM v_recipes WHERE (complete OR ?)"
    params: list = [args.all]
    if args.profession:
        sql += " AND profession LIKE ?"
        params.append(f"{args.profession}%")
    if args.search:
        sql += " AND (name LIKE ? OR product LIKE ?)"
        params += [f"%{args.search}%"] * 2
    if args.skill is not None:
        # Recipes that still give skill-ups (not grey) at the given skill level.
        sql += " AND coalesce(min_skill, 0) <= ? AND (skill_grey IS NULL OR skill_grey > ?)"
        params += [args.skill, args.skill]
    sql += " ORDER BY profession, skill_yellow, name"
    for r in conn.execute(sql, params):
        skill = f"{r['skill_yellow'] or '-'}/{r['skill_green'] or '-'}/{r['skill_grey'] or '-'}"
        product = f" -> {_fmt_qty(r['product_count'])}x {r['product']}" if r["product"] else ""
        print(f"{r['spell_id']:>6}  {r['profession'][:12]:12} {skill:>11}  {r['name']}{product}")
        if args.verbose:
            print(f"{'':8}reagents: {r['reagents'] or '-'}")
            if r["taught_by"]:
                print(f"{'':8}taught by: {r['taught_by']}")


def cmd_uses(args) -> None:
    conn = open_gamedata()
    item_id = resolve_item(conn, args.item)
    rows = conn.execute(
        "SELECT * FROM v_reagent_usage WHERE reagent_id = ? AND (complete OR ?) ORDER BY profession, skill_yellow",
        (item_id, args.all),
    ).fetchall()
    if not rows:
        print("Not used in any recipe.")
    for r in rows:
        print(f"{r['spell_id']:>6}  {r['profession'][:12]:12} {r['skill_yellow'] or '-':>4}  {r['count']}x  {r['recipe']}")


def _print_quote(q: Quote, depth: int = 0) -> None:
    alts = ", ".join(f"{m} {fmt_money(c)}" for m, c in q.options.items() if m != q.method)
    alts = f"  (alt: {alts})" if alts else ""
    print(f"{'  ' * depth}{_fmt_qty(q.quantity)}x {q.name}: {fmt_money(q.total)} [{q.method}]{alts}")
    for c in q.components:
        _print_quote(c, depth + 1)


def _print_craft(cost: CraftCost) -> None:
    print(f"{cost.name} x{_fmt_qty(cost.crafts)}: reagents {fmt_money(cost.total)}")
    for c in cost.components:
        _print_quote(c, 1)


def cmd_cost(args) -> None:
    conn = open_gamedata()
    _print_quote(_make_resolver(conn, args).quote(resolve_item(conn, args.item), args.quantity))


def cmd_profit(args) -> None:
    conn = open_gamedata()
    resolver = _make_resolver(conn, args)
    spells = [
        r[0]
        for r in conn.execute(
            "SELECT r.spell_id FROM recipes r JOIN professions p ON p.id = r.profession_id "
            "WHERE p.name LIKE ? AND r.product_item_id IS NOT NULL AND r.complete",
            (f"{args.profession}%",),
        )
    ]
    results = [resolver.profit(s) for s in spells]
    results = [p for p in results if p.profit is not None]
    results.sort(key=lambda p: p.profit, reverse=True)
    if not results:
        print("No recipes with both product and reagent prices. Import some AH data first.")
    for p in results[: args.limit]:
        print(f"{fmt_money(p.profit):>14}  sells {fmt_money(p.revenue):>12}  costs {fmt_money(p.cost.total):>12}  {p.recipe}")


def cmd_prices_import(args) -> None:
    adapter = ADAPTERS[args.adapter](Path(args.path))
    conn = open_prices()
    n = store_snapshots(conn, adapter.read(), source=args.source or adapter.name, realm=args.realm, faction=args.faction)
    print(f"Imported {n} new snapshots into {config.PRICES_DB}")


def cmd_serve(args) -> None:
    exe = shutil.which("datasette")
    if not exe:
        sys.exit("datasette not installed; run `uv sync --extra ui`")
    open_prices().close()
    cmd = [exe, "serve", str(config.GAMEDATA_DB), str(config.PRICES_DB), "--crossdb",
           "-m", str(config.PROJECT_ROOT / "datasette" / "metadata.json"), "-p", str(args.port)]
    if args.open:
        cmd.append("--open")
    subprocess.run(cmd, check=False)


def cmd_ui(args) -> None:
    import webbrowser

    from recipe_explorer.ui.server import STATIC_DIR, make_server

    if not config.GAMEDATA_DB.exists():
        sys.exit(f"{config.GAMEDATA_DB} not found; run `rex build` first")
    if not (STATIC_DIR / "index.html").exists():
        print("warning: frontend not built; run `pnpm --dir web install && pnpm --dir web build`")
    server = make_server(args.host, args.port)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Recipe chains UI at {url}  (Ctrl-C to stop)")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# --- parser --------------------------------------------------------------------

ALL_HELP = "include recipes referencing items missing from the client (usually SoD/removed content)"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rex", description="WoW Forever recipe explorer")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("builds", help="list Forever client builds on wago.tools")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.set_defaults(func=cmd_builds)

    s = sub.add_parser("fetch", help="download raw DB2 CSVs for a build")
    s.add_argument("--build", default="latest")
    s.add_argument("--force", action="store_true", help="re-download existing files")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("build", help="fetch (if needed) and (re)build gamedata.sqlite")
    s.add_argument("--build", default="latest")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("info", help="show build info and recipe counts")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("recipes", help="list/search recipes")
    s.add_argument("-p", "--profession")
    s.add_argument("-s", "--search")
    s.add_argument("--skill", type=int, help="only recipes still giving skill-ups at this level")
    s.add_argument("-v", "--verbose", action="store_true")
    s.add_argument("--all", action="store_true", help=ALL_HELP)
    s.set_defaults(func=cmd_recipes)

    s = sub.add_parser("uses", help="recipes that use an item as a reagent")
    s.add_argument("item", help="item ID or name")
    s.add_argument("--all", action="store_true", help=ALL_HELP)
    s.set_defaults(func=cmd_uses)

    def price_opts(sp):
        sp.add_argument("--realm", help="realm for AH prices (omit to ignore AH data)")
        sp.add_argument("--faction", default="", choices=["", "alliance", "horde"])
        sp.add_argument("--max-age-days", type=float, help="ignore AH data older than this")

    s = sub.add_parser("cost", help="cheapest way to obtain an item (AH / vendor / craft)")
    s.add_argument("item", help="item ID or name")
    s.add_argument("-q", "--quantity", type=float, default=1)
    price_opts(s)
    s.set_defaults(func=cmd_cost)

    s = sub.add_parser("profit", help="rank a profession's recipes by AH profit")
    s.add_argument("profession")
    s.add_argument("-n", "--limit", type=int, default=25)
    price_opts(s)
    s.set_defaults(func=cmd_profit)

    prices = sub.add_parser("prices", help="manage AH price data").add_subparsers(dest="prices_cmd", required=True)
    s = prices.add_parser("import", help="import prices with an adapter")
    s.add_argument("adapter", choices=sorted(ADAPTERS))
    s.add_argument("path")
    s.add_argument("--realm", required=True)
    s.add_argument("--faction", default="", choices=["", "alliance", "horde"])
    s.add_argument("--source", help="label stored with snapshots (default: adapter name)")
    s.set_defaults(func=cmd_prices_import)

    s = sub.add_parser("serve", help="browse the data in Datasette")
    s.add_argument("--port", type=int, default=8001)
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("ui", help="crafting chain graph in the browser")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8002)
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_ui)
    return p


def main(argv: list[str] | None = None) -> None:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # behave when piped into `head`
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
