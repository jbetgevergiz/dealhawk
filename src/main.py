import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler

def setup_logging():
    log_dir = os.environ.get("LOG_DIR", "/app/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "dealhawk.log")

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Rotating file handler (daily, 14 days)
    file_handler = TimedRotatingFileHandler(log_file, when="midnight", backupCount=14)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    return logging.getLogger("dealhawk.main")


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("DealHawk starting up...")

    # Load config
    from .config import load_config
    config = load_config()
    logger.info(f"Config loaded: {len(config['components'])} components")

    # Init DB
    from .db import init_db, seed_components
    db_path = os.environ.get("DB_PATH", "/app/data/dealhawk.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = init_db(db_path)
    seed_components(conn, config["components"])
    logger.info(f"DB initialized: {db_path}")

    # Send startup Telegram ping
    from .alerter import send_startup_ping
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        ok = send_startup_ping(token, chat_id, component_count=len(config["components"]))
        logger.info(f"Startup ping sent: {ok}")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping startup ping")

    # Build scrapers
    from .scrapers.pcpartpicker import PCPartPickerScraper
    from .scrapers.amazon import AmazonScraper
    from .scrapers.newegg import NeweggScraper
    scrapers = {
        "pcpartpicker": PCPartPickerScraper(config),
        "amazon": AmazonScraper(config),
        "newegg": NeweggScraper(config),
    }
    alerter_config = {"token": token, "chat_id": chat_id}

    # Immediate scrape cycle on startup
    logger.info("Running immediate scrape cycle...")
    from .scheduler import scrape_all_components
    try:
        scrape_all_components(config, conn, scrapers, alerter_config)
    except Exception as e:
        logger.error(f"Initial scrape failed: {e}", exc_info=True)

    # Start APScheduler
    from .scheduler import setup_scheduler
    scheduler = setup_scheduler(config, conn, scrapers, alerter_config)
    scheduler.start()
    logger.info(f"Scheduler started: every {config['scraping']['interval_hours']}h")

    # Start Flask healthcheck (blocks main thread)
    from .healthcheck import start_healthcheck
    logger.info("Starting healthcheck server on :8085")
    start_healthcheck(conn, config, host="0.0.0.0", port=8085)


if __name__ == "__main__":
    main()
