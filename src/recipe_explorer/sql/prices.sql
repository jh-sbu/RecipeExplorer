-- Auction house price history. Long-lived: NEVER dropped when game data is rebuilt.
-- Item IDs are stable across builds, so no foreign keys into gamedata.

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id           INTEGER PRIMARY KEY,
    source       TEXT    NOT NULL,          -- adapter name, e.g. 'csv', 'auctionator'
    realm        TEXT    NOT NULL,
    faction      TEXT    NOT NULL DEFAULT '',  -- 'alliance' | 'horde' | '' for shared/unknown
    observed_at  TEXT    NOT NULL,          -- ISO-8601 UTC
    item_id      INTEGER NOT NULL,
    min_buyout   INTEGER,                   -- copper per unit
    market_value INTEGER,                   -- copper per unit (source-defined average)
    quantity     INTEGER,
    imported_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (source, realm, faction, observed_at, item_id)
);
CREATE INDEX IF NOT EXISTS price_snapshots_lookup
    ON price_snapshots (item_id, realm, faction, observed_at);

-- Most recent snapshot per (item, realm, faction), across all sources.
CREATE VIEW IF NOT EXISTS latest_prices AS
SELECT ps.*
  FROM price_snapshots ps
 WHERE ps.id = (
        SELECT p2.id FROM price_snapshots p2
         WHERE p2.item_id = ps.item_id AND p2.realm = ps.realm AND p2.faction = ps.faction
         ORDER BY p2.observed_at DESC, p2.id DESC
         LIMIT 1);
