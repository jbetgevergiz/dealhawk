import logging
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def calculate_rolling_average(conn, component_id: int, lookback_days: int) -> Optional[float]:
    """
    Calculate rolling average of the lowest price per scrape session.
    Requires at least 5 data points. Only uses is_valid=TRUE prices.
    """
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("""
        SELECT MIN(price) as session_low
        FROM prices
        WHERE component_id = ?
          AND is_valid = 1
          AND scraped_at > ?
        GROUP BY strftime('%Y-%m-%dT%H', scraped_at)
        ORDER BY scraped_at DESC
    """, (component_id, cutoff)).fetchall()

    if len(rows) < 5:
        logger.debug(f"Component {component_id}: only {len(rows)} data points, need 5+")
        return None

    prices = [r["session_low"] for r in rows]
    return sum(prices) / len(prices)


def detect_deal(current_price: float, rolling_avg: Optional[float], threshold_pct: float) -> Optional[dict]:
    """
    Returns deal info dict if current_price is below rolling_avg by threshold_pct, else None.
    """
    if rolling_avg is None or rolling_avg <= 0:
        return None
    drop_pct = ((rolling_avg - current_price) / rolling_avg) * 100
    if drop_pct < threshold_pct:
        return None
    return {
        "current_price": current_price,
        "rolling_avg": rolling_avg,
        "drop_pct": round(drop_pct, 2),
        "is_deal": True
    }


def check_freshness(conn, component_id: int, staleness_ttl_hours: int) -> dict:
    """
    Returns freshness info for a component.
    Status: 'fresh', 'stale', 'no_data'
    """
    from .db import get_consecutive_failures

    row = conn.execute("""
        SELECT scraped_at FROM prices
        WHERE component_id = ? AND is_valid = 1
        ORDER BY scraped_at DESC LIMIT 1
    """, (component_id,)).fetchone()

    consecutive_failures = get_consecutive_failures(conn, component_id)

    if not row:
        return {
            "status": "no_data",
            "last_successful_scrape": None,
            "hours_since": None,
            "consecutive_failures": consecutive_failures
        }

    last_scrape = row["scraped_at"]
    try:
        last_dt = datetime.strptime(last_scrape, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        last_dt = datetime.fromisoformat(last_scrape)

    hours_since = (datetime.utcnow() - last_dt).total_seconds() / 3600

    if hours_since <= staleness_ttl_hours:
        status = "fresh"
    else:
        status = "stale"

    return {
        "status": status,
        "last_successful_scrape": last_scrape,
        "hours_since": round(hours_since, 2),
        "consecutive_failures": consecutive_failures
    }
