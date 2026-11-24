"""Local web UI: a small JSON API over the chain planner, plus the built frontend.

Standard library only. Each request opens its own read-only SQLite connection, so the
threading server needs no locking. Bound to localhost by default.

  GET  /api/meta                  build info, realms with price data
  GET  /api/items?q=silk          item search (craftable items first)
  POST /api/graph                 {targets, overrides, realm, faction, max_age_days, ah_cut}
  GET  /api/uses?item_id=…        what an item can be made into, ranked by value per unit
                                  (&realm &faction &max_age_days &ah_cut &depth &min_listings)
  GET  /*                         frontend files from ui/static (built from web/)
"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from recipe_explorer import config
from recipe_explorer.db import open_gamedata
from recipe_explorer.prices import Override, PriceResolver, build_chain, find_uses
from recipe_explorer.prices.uses import MAX_DEPTH

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 1 << 20


class ApiError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


# --- API handlers (pure functions of a connection + input, for testing) -----------


def api_meta(conn: sqlite3.Connection) -> dict:
    meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    realms = [
        {"realm": r["realm"], "faction": r["faction"], "items": r["items"], "newest": r["newest"]}
        for r in conn.execute(
            "SELECT realm, faction, count(DISTINCT item_id) items, max(observed_at) newest "
            "FROM prices.price_snapshots GROUP BY realm, faction ORDER BY realm, faction"
        )
    ]
    return {"meta": meta, "realms": realms}


def api_items(conn: sqlite3.Connection, q: str, limit: int = 25) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    if q.isdigit():
        where, params = "i.id = ?", [int(q)]
    else:
        where, params = "i.name LIKE ?", [f"%{q}%"]
    rows = conn.execute(
        f"""
        SELECT i.id, i.name, i.quality,
               EXISTS (SELECT 1 FROM recipes r WHERE r.product_item_id = i.id AND r.complete) AS craftable
          FROM items i
         WHERE {where} AND i.in_client
         ORDER BY craftable DESC, (i.name LIKE ?) DESC, length(i.name), i.name
         LIMIT ?
        """,
        [*params, f"{q}%", limit],
    ).fetchall()
    return [dict(r) for r in rows]


def _num(value, name: str, *, cast=float, minimum: float | None = None):
    try:
        v = cast(value)
    except (TypeError, ValueError):
        raise ApiError(f"{name} must be a number") from None
    if minimum is not None and v < minimum:
        raise ApiError(f"{name} must be >= {minimum}")
    return v


def api_graph(conn: sqlite3.Connection, body: dict) -> dict:
    targets = []
    for t in body.get("targets") or []:
        item_id = _num(t.get("item_id"), "item_id", cast=int)
        qty = _num(t.get("quantity", 1), "quantity", minimum=0)
        if qty > 0:
            targets.append((item_id, qty))
    known = {
        r[0]
        for r in conn.execute(
            f"SELECT id FROM items WHERE id IN ({','.join('?' * len(targets))})", [t[0] for t in targets]
        )
    } if targets else set()
    if missing := [t[0] for t in targets if t[0] not in known]:
        raise ApiError(f"unknown item id(s): {missing}", HTTPStatus.NOT_FOUND)

    overrides = {}
    for key, o in (body.get("overrides") or {}).items():
        method = o.get("method")
        if method not in ("market", "vendor", "craft"):
            raise ApiError(f"override method must be market, vendor or craft, not {method!r}")
        recipe = o.get("recipe")
        overrides[_num(key, "override item", cast=int)] = Override(
            method, None if recipe is None else _num(recipe, "recipe", cast=int)
        )

    resolver = _resolver(conn, body)
    return build_chain(resolver, targets, overrides).to_json(resolver.ah_cut)


def _resolver(conn: sqlite3.Connection, params: dict) -> PriceResolver:
    """Price settings shared by every endpoint: realm, faction, max_age_days, ah_cut."""
    max_age = params.get("max_age_days")
    return PriceResolver(
        conn,
        realm=params.get("realm") or None,
        faction=params.get("faction") or "",
        max_age=timedelta(days=_num(max_age, "max_age_days", minimum=0)) if max_age else None,
        ah_cut=_num(params.get("ah_cut", 0.05), "ah_cut", minimum=0),
    )


def api_uses(conn: sqlite3.Connection, params: dict) -> dict:
    item_id = _num(params.get("item_id"), "item_id", cast=int)
    if conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
        raise ApiError(f"unknown item id: {item_id}", HTTPStatus.NOT_FOUND)
    depth = _num(params.get("depth", 2), "depth", cast=int, minimum=1)
    if depth > MAX_DEPTH:
        raise ApiError(f"depth must be <= {MAX_DEPTH}")
    min_listings = _num(params.get("min_listings") or 0, "min_listings", cast=int, minimum=0)
    return find_uses(_resolver(conn, params), item_id, depth, min_listings).to_json()


# --- HTTP -------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "rex-ui"
    gamedata_path: Path = config.GAMEDATA_DB
    prices_path: Path = config.PRICES_DB
    static_dir: Path = STATIC_DIR

    def log_message(self, fmt, *args) -> None:  # quieter than the default
        if not self.path.startswith("/api/"):
            return
        super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _api(self, fn) -> None:
        try:
            conn = open_gamedata(self.gamedata_path, prices_path=self.prices_path)
        except FileNotFoundError as e:
            return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(e)})
        try:
            self._json(HTTPStatus.OK, fn(conn))
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        finally:
            conn.close()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/meta":
            return self._api(api_meta)
        if url.path == "/api/items":
            return self._api(lambda c: api_items(c, query.get("q", [""])[0]))
        if url.path == "/api/uses":
            return self._api(lambda c: api_uses(c, {k: v[0] for k, v in query.items()}))
        if url.path.startswith("/api/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "no such endpoint"})
        self._static(url.path)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path != "/api/graph":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "no such endpoint"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be a JSON object"})
        self._api(lambda c: api_graph(c, body))

    def _static(self, path: str) -> None:
        root = self.static_dir.resolve()
        if not (root / "index.html").exists():
            msg = b"Frontend not built. Run: pnpm --dir web install && pnpm --dir web build\n"
            return self._send(HTTPStatus.SERVICE_UNAVAILABLE, msg, "text/plain; charset=utf-8")
        target = (root / path.lstrip("/")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            target = root / "index.html"  # single-page app: unknown paths get the shell
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)


def make_server(host: str = "127.0.0.1", port: int = 8002, **paths: Path) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), paths)
    return ThreadingHTTPServer((host, port), handler)
