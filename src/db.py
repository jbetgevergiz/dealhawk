import sqlite3
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List
from .scrapers.base import ScrapeResult, ScrapeOutcome

logger = logging.getLogger(__name__)

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS components (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            search_terms TEXT NOT NULL DEFAULT '[]',
            filters TEXT NOT NULL DEFAULT '{}',
            drop_threshold_pct REAL NOT NULL DEFAULT 15.0,
            lookback_days INTEGER NOT NULL DEFAULT 30,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component_id INTEGER NOT NULL REFERENCES components(id),
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            retailer TEXT NOT NULL,
            source TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            scraped_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_valid INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component_id INTEGER NOT NULL REFERENCES components(id),
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT,
            http_status_code INTEGER,
            response_time_ms INTEGER NOT NULL DEFAULT 0,
            results_count INTEGER NOT NULL DEFAULT 0,
            attempted_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component_id INTEGER NOT NULL REFERENCES components(id),
            alert_type TEXT NOT NULL,
            product_name TEXT,
            current_price REAL,
            rolling_avg REAL,
            drop_pct REAL,
            retailer TEXT,
            url TEXT,
            channel TEXT NOT NULL DEFAULT 'telegram',
            sent_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Stores current best deal + 2 previous bests per component for URL redundancy
        CREATE TABLE IF NOT EXISTS best_deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            component_id INTEGER NOT NULL REFERENCES components(id),
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            retailer TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            rank INTEGER NOT NULL DEFAULT 1,  -- 1=current best, 2=second best, 3=third best
            recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(component_id, rank)
        );

        CREATE INDEX IF NOT EXISTS idx_prices_scraped_at ON prices(scraped_at);
        CREATE INDEX IF NOT EXISTS idx_prices_component_id ON prices(component_id);
        CREATE INDEX IF NOT EXISTS idx_scrape_log_attempted_at ON scrape_log(attempted_at);
        CREATE INDEX IF NOT EXISTS idx_scrape_log_component_id ON scrape_log(component_id);
        CREATE INDEX IF NOT EXISTS idx_best_deals_component_id ON best_deals(component_id);
    """)

    # Create views
    c.execute("DROP VIEW IF EXISTS current_prices")
    c.execute("""
        CREATE VIEW current_prices AS
        WITH ranked AS (
            SELECT p.*,
                   ROW_NUMBER() OVER (PARTITION BY component_id ORDER BY scraped_at DESC) AS rn,
                   ROUND((julianday('now') - julianday(scraped_at)) * 24, 2) AS hours_ago,
                   CASE WHEN scraped_at > datetime('now', '-6 hours') THEN 'fresh' ELSE 'stale' END AS freshness_status
            FROM prices
            WHERE is_valid = 1
        )
        SELECT id, component_id, product_name, price, retailer, source, url,
               scraped_at, is_valid, hours_ago, freshness_status
        FROM ranked WHERE rn <= 5
    """)

    c.execute("DROP VIEW IF EXISTS scrape_health")
    c.execute("""
        CREATE VIEW scrape_health AS
        WITH ranked AS (
            SELECT s.*,
                   ROW_NUMBER() OVER (PARTITION BY component_id ORDER BY attempted_at DESC) AS rn
            FROM scrape_log s
        )
        SELECT * FROM ranked WHERE rn <= 3
    """)

    conn.commit()
    return conn


def seed_components(conn: sqlite3.Connection, components_config: list):
    """Insert components from config if they don't exist."""
    c = conn.cursor()
    for comp in components_config:
        c.execute("""
            INSERT OR IGNORE INTO components (name, category, search_terms, filters, drop_threshold_pct, lookback_days)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            comp["name"],
            comp.get("category", ""),
            json.dumps(comp.get("search_terms", [])),
            json.dumps(comp.get("filters", {})),
            comp.get("drop_threshold_pct", 15.0),
            comp.get("lookback_days", 30),
        ))
    conn.commit()


def get_component_id(conn: sqlite3.Connection, name: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM components WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def get_all_components(conn: sqlite3.Connection) -> list:
    rows = conn.execute("SELECT * FROM components").fetchall()
    return [dict(r) for r in rows]


def insert_price(conn: sqlite3.Connection, component_id: int, result: ScrapeResult):
    conn.execute("""
        INSERT INTO prices (component_id, product_name, price, retailer, source, url, scraped_at, is_valid)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 1)
    """, (component_id, result.product_name, result.price, result.retailer, result.source, result.url))
    conn.commit()
    # Update best_deals table after every new price insertion
    _update_best_deals(conn, component_id)


def _update_best_deals(conn: sqlite3.Connection, component_id: int):
    """
    Keep a rolling top-3 best deals per component.
    rank 1 = current best price (with URL)
    rank 2 = second best
    rank 3 = third best
    Only includes records that have a URL.
    """
    rows = conn.execute("""
        SELECT product_name, price, retailer, url
        FROM prices
        WHERE component_id = ?
          AND is_valid = 1
          AND url != ''
          AND url IS NOT NULL
          AND scraped_at > datetime('now', '-7 days')
        ORDER BY price ASC
        LIMIT 3
    """, (component_id,)).fetchall()

    if not rows:
        return

    for rank, row in enumerate(rows, start=1):
        conn.execute("""
            INSERT INTO best_deals (component_id, product_name, price, retailer, url, rank, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(component_id, rank) DO UPDATE SET
                product_name = excluded.product_name,
                price = excluded.price,
                retailer = excluded.retailer,
                url = excluded.url,
                recorded_at = excluded.recorded_at
        """, (component_id, row["product_name"], row["price"], row["retailer"], row["url"], rank))

    conn.commit()


def get_best_deals(conn: sqlite3.Connection, component_id: int) -> list:
    """Return top 3 best deals with URLs for a component."""
    rows = conn.execute("""
        SELECT rank, product_name, price, retailer, url, recorded_at
        FROM best_deals
        WHERE component_id = ?
        ORDER BY rank ASC
    """, (component_id,)).fetchall()
    return [dict(r) for r in rows]


def rebuild_all_best_deals(conn: sqlite3.Connection):
    """Rebuild best_deals table from existing price data. Run once on startup."""
    components = conn.execute("SELECT id FROM components").fetchall()
    for comp in components:
        _update_best_deals(conn, comp["id"])
    logger.info("Rebuilt best_deals table for all components")


def log_scrape(conn: sqlite3.Connection, component_id: int, source: str, outcome: ScrapeOutcome):
    conn.execute("""
        INSERT INTO scrape_log (component_id, source, status, error_message, http_status_code,
                                response_time_ms, results_count, attempted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        component_id,
        source,
        outcome.status,
        outcome.error_message,
        outcome.http_status_code,
        outcome.response_time_ms,
        len(outcome.results),
    ))
    conn.commit()


def log_alert(conn: sqlite3.Connection, component_id: int, alert_type: str,
              product_name: str = None, current_price: float = None, rolling_avg: float = None,
              drop_pct: float = None, retailer: str = None, url: str = None, channel: str = "telegram"):
    conn.execute("""
        INSERT INTO alerts (component_id, alert_type, product_name, current_price, rolling_avg,
                            drop_pct, retailer, url, channel, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (component_id, alert_type, product_name, current_price, rolling_avg, drop_pct, retailer, url, channel))
    conn.commit()


def check_alert_cooldown(conn: sqlite3.Connection, component_id: int, hours: int) -> bool:
    """Returns True if cooldown is active (i.e., an alert was sent recently)."""
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM alerts
        WHERE component_id = ? AND alert_type = 'deal' AND sent_at > ?
    """, (component_id, cutoff)).fetchone()
    return row["cnt"] > 0


def get_consecutive_failures(conn: sqlite3.Connection, component_id: int) -> int:
    """Count consecutive failed/blocked/timeout/no_results from most recent scrape logs."""
    rows = conn.execute("""
        SELECT status FROM scrape_log
        WHERE component_id = ?
        ORDER BY attempted_at DESC
        LIMIT 20
    """, (component_id,)).fetchall()

    count = 0
    for row in rows:
        if row["status"] == "success":
            break
        count += 1
    return count
