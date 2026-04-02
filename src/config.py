import os
import yaml
import logging

logger = logging.getLogger(__name__)

REQUIRED_TOP_LEVEL = ["alerts", "scraping", "freshness", "components"]
REQUIRED_ALERT_KEYS = ["default_drop_threshold_pct", "default_lookback_days", "alert_cooldown_hours", "failure_alert_threshold"]

def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", "/app/config.yml")
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    if not config:
        raise ValueError("Config file is empty")

    # Validate top-level keys
    for key in REQUIRED_TOP_LEVEL:
        if key not in config:
            raise ValueError(f"Missing required config key: {key}")

    # Validate alert keys
    for key in REQUIRED_ALERT_KEYS:
        if key not in config.get("alerts", {}):
            raise ValueError(f"Missing required alerts config key: {key}")

    if not config.get("components"):
        raise ValueError("No components defined in config")

    # Apply defaults to components
    for comp in config["components"]:
        comp.setdefault("drop_threshold_pct", config["alerts"]["default_drop_threshold_pct"])
        comp.setdefault("lookback_days", config["alerts"]["default_lookback_days"])

    logger.info(f"Config loaded: {len(config['components'])} components")
    return config
