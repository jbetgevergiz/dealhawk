import logging
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

EST = pytz.timezone("America/New_York")

def _get_est_timestamp() -> str:
    return datetime.now(EST).strftime("%Y-%m-%d %H:%M:%S %Z")

def send_telegram(token: str, chat_id: str, message: str) -> bool:
    """Send a Telegram message using the Bot API (sync, no dependency on python-telegram-bot async)."""
    import requests
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            logger.info(f"Telegram message sent OK (chat_id={chat_id})")
            return True
        else:
            logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram send exception: {e}")
        return False

def format_deal_alert(component_name: str, price: float, avg: float, drop_pct: float,
                      product_name: str, retailer: str, url: str) -> str:
    return (
        f"🔥 <b>DEAL ALERT: {component_name}</b>\n"
        f"💰 Current Price: <b>${price:.2f}</b>\n"
        f"📊 30-Day Average: ${avg:.2f}\n"
        f"📉 Drop: <b>{drop_pct:.1f}%</b>\n"
        f"🏷️ {product_name}\n"
        f"🛒 {retailer.title()}\n"
        f"🔗 {url}\n"
        f"⏰ Detected: {_get_est_timestamp()}"
    )

def format_failure_alert(component_name: str, n_failures: int, hours_ago, last_error: str) -> str:
    hours_str = f"{hours_ago:.1f}" if hours_ago is not None else "unknown"
    return (
        f"⚠️ <b>SCRAPE FAILURE: {component_name}</b>\n"
        f"❌ {n_failures} consecutive failures\n"
        f"📍 Last success: {hours_str} hours ago\n"
        f"🔧 Last error: {last_error or 'unknown'}\n"
        f"Data for this component is now <b>STALE</b>."
    )

def send_startup_ping(token: str, chat_id: str, component_count: int = 0) -> bool:
    msg = f"🟢 <b>DealHawk online</b> — watching {component_count} component(s) for deals"
    return send_telegram(token, chat_id, msg)
