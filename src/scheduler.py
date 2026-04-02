import logging
import os
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

# Module-level state for healthcheck
last_scrape_run = None

def scrape_all_components(config: dict, conn, scrapers: dict, alerter_config: dict):
    """
    For each component:
      1. Try PCPartPicker
      2. Fallback to Amazon
      3. Fallback to Newegg
      4. Log every attempt
      5. Insert prices on success
      6. Check failures and deal alerts
    """
    global last_scrape_run
    from .db import (insert_price, log_scrape, log_alert, get_component_id,
                     check_alert_cooldown, get_consecutive_failures)
    from .analysis import calculate_rolling_average, detect_deal, check_freshness
    from .alerter import send_telegram, format_deal_alert, format_failure_alert

    token = alerter_config["token"]
    chat_id = alerter_config["chat_id"]
    failure_threshold = config["alerts"]["failure_alert_threshold"]
    cooldown_hours = config["alerts"]["alert_cooldown_hours"]
    staleness_ttl = config["freshness"]["staleness_ttl_hours"]

    pcp_scraper = scrapers.get("pcpartpicker")
    amz_scraper = scrapers.get("amazon")
    neg_scraper = scrapers.get("newegg")

    for component in config["components"]:
        comp_name = component["name"]
        comp_id = get_component_id(conn, comp_name)
        if comp_id is None:
            logger.error(f"Component not found in DB: {comp_name}")
            continue

        logger.info(f"Scraping: {comp_name}")
        outcome = None
        source_used = None

        # 1. Try PCPartPicker
        if pcp_scraper:
            outcome = pcp_scraper.scrape(component)
            log_scrape(conn, comp_id, "pcpartpicker", outcome)
            if outcome.status == "success" and outcome.results:
                source_used = "pcpartpicker"
            else:
                logger.info(f"{comp_name}: PCPartPicker status={outcome.status}, trying Amazon")

        # 2. Fallback to Amazon
        if source_used is None and amz_scraper:
            outcome = amz_scraper.scrape(component)
            log_scrape(conn, comp_id, "amazon", outcome)
            if outcome.status == "success" and outcome.results:
                source_used = "amazon"
            else:
                logger.info(f"{comp_name}: Amazon status={outcome.status}, trying Newegg")

        # 3. Fallback to Newegg
        if source_used is None and neg_scraper:
            outcome = neg_scraper.scrape(component)
            log_scrape(conn, comp_id, "newegg", outcome)
            if outcome.status == "success" and outcome.results:
                source_used = "newegg"

        # 5. Insert prices on success
        if source_used and outcome and outcome.results:
            for result in outcome.results:
                insert_price(conn, comp_id, result)
            logger.info(f"{comp_name}: inserted {len(outcome.results)} prices from {source_used}")
        else:
            logger.warning(f"{comp_name}: all scrapers failed")

        # 6. Check consecutive failures
        consecutive = get_consecutive_failures(conn, comp_id)
        if consecutive >= failure_threshold:
            freshness = check_freshness(conn, comp_id, staleness_ttl)
            # Use failure cooldown key
            cutoff_row = conn.execute("""
                SELECT COUNT(*) as cnt FROM alerts
                WHERE component_id = ? AND alert_type = 'failure'
                  AND sent_at > datetime('now', ?)
            """, (comp_id, f"-{cooldown_hours} hours")).fetchone()
            failure_cooldown_active = cutoff_row["cnt"] > 0 if cutoff_row else False

            if not failure_cooldown_active:
                last_error = outcome.error_message if outcome else "Unknown"
                msg = format_failure_alert(
                    component_name=comp_name,
                    n_failures=consecutive,
                    hours_ago=freshness.get("hours_since"),
                    last_error=last_error
                )
                if send_telegram(token, chat_id, msg):
                    log_alert(conn, comp_id, alert_type="failure", channel="telegram")
                    logger.info(f"Failure alert sent for {comp_name}")

        # 7. Check for deals
        if source_used and outcome and outcome.results:
            lookback = component.get("lookback_days", config["alerts"]["default_lookback_days"])
            threshold = component.get("drop_threshold_pct", config["alerts"]["default_drop_threshold_pct"])
            rolling_avg = calculate_rolling_average(conn, comp_id, lookback)

            for result in outcome.results:
                deal = detect_deal(result.price, rolling_avg, threshold)
                if deal:
                    cooldown_active = check_alert_cooldown(conn, comp_id, cooldown_hours)
                    if not cooldown_active:
                        msg = format_deal_alert(
                            component_name=comp_name,
                            price=result.price,
                            avg=deal["rolling_avg"],
                            drop_pct=deal["drop_pct"],
                            product_name=result.product_name,
                            retailer=result.retailer,
                            url=result.url
                        )
                        if send_telegram(token, chat_id, msg):
                            log_alert(conn, comp_id, alert_type="deal",
                                      product_name=result.product_name,
                                      current_price=result.price,
                                      rolling_avg=rolling_avg,
                                      drop_pct=deal["drop_pct"],
                                      retailer=result.retailer,
                                      url=result.url,
                                      channel="telegram")
                            logger.info(f"Deal alert sent for {comp_name} @ ${result.price:.2f}")
                        break  # One deal alert per component per run

        time.sleep(1)  # Brief pause between components

    last_scrape_run = datetime.utcnow().isoformat()
    logger.info("Scrape cycle complete")


def setup_scheduler(config: dict, conn, scrapers: dict, alerter_config: dict) -> BackgroundScheduler:
    interval_hours = config["scraping"]["interval_hours"]
    scheduler = BackgroundScheduler(timezone="America/New_York")
    scheduler.add_job(
        scrape_all_components,
        trigger="interval",
        hours=interval_hours,
        args=[config, conn, scrapers, alerter_config],
        id="scrape_all",
        name="Scrape all components",
        max_instances=1,
        coalesce=True,
    )
    return scheduler
