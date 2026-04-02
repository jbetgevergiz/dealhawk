import time
import random
import logging
import re
import requests
from bs4 import BeautifulSoup
from .base import BaseScraper, ScrapeResult, ScrapeOutcome

logger = logging.getLogger(__name__)

# Per-component price floor filters — prevents garbage results like $0.99 accessories
# Customize per your tracked components' categories
PRICE_FLOORS = {
    "video-card": 300.0,
    "memory": 50.0,
    "cpu-cooler": 20.0,
    "internal-hard-drive": 100.0,
    "case": 50.0,
    "power-supply": 80.0,
}

# Keywords that MUST appear in product name (ANY match = pass)
REQUIRED_KEYWORDS: dict = {
    # Example:
    # "memory": ["64gb", "64 gb"],
}

# Keywords that must NOT appear in product name
BLACKLIST_KEYWORDS: dict = {
    # Example:
    # "internal-hard-drive": ["512gb", "256gb"],
}

def get_price_floor(component_config: dict) -> float:
    name = component_config.get("name", "").lower()
    category = component_config.get("category", "").lower()
    for key, floor in PRICE_FLOORS.items():
        if key in name:
            return floor
    return PRICE_FLOORS.get(category, 0.0)

def passes_keyword_filter(product_name: str, component_config: dict) -> bool:
    category = component_config.get("category", "").lower()
    name_lower = product_name.lower()
    required = REQUIRED_KEYWORDS.get(category, [])
    if required and not any(kw in name_lower for kw in required):
        return False
    blacklist = BLACKLIST_KEYWORDS.get(category, [])
    if any(kw in name_lower for kw in blacklist):
        return False
    return True

def clean_amazon_url(href: str) -> str:
    if not href:
        return ""
    asin_match = re.search(r'/dp/([A-Z0-9]{10})', href)
    if asin_match:
        return f"https://www.amazon.com/dp/{asin_match.group(1)}"
    if href.startswith("/") and ("/dp/" in href or "/gp/" in href):
        return "https://www.amazon.com" + href.split("?")[0]
    if href.startswith("https://www.amazon.com"):
        return href.split("?")[0]
    return ""


class AmazonScraper(BaseScraper):
    BASE_URL = "https://www.amazon.com/s?k={term}&ref=nb_sb_noss"

    def __init__(self, config: dict):
        self.user_agents = config.get("scraping", {}).get("user_agents", [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ])
        self.timeout = config.get("scraping", {}).get("request_timeout_seconds", 30)
        self.delay = config.get("scraping", {}).get("request_delay_seconds", 3)

        try:
            from curl_cffi import requests as cffi_requests
            self._session = cffi_requests.Session(impersonate="chrome124")
            self._use_cffi = True
            logger.info("AmazonScraper: using curl_cffi (Chrome TLS impersonation)")
        except ImportError:
            self._session = requests.Session()
            self._use_cffi = False
            logger.warning("AmazonScraper: curl_cffi not installed")

    def _get_headers(self):
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

    def scrape(self, component_config: dict) -> ScrapeOutcome:
        search_terms = component_config.get("search_terms", [])
        if not search_terms:
            return ScrapeOutcome(status="failed", error_message="No search terms provided")

        price_floor = get_price_floor(component_config)
        all_results = []
        last_status = "no_results"
        last_error = None
        last_http_code = None
        total_ms = 0

        for term in search_terms[:1]:
            url = self.BASE_URL.format(term=requests.utils.quote(term))
            start = time.time()
            try:
                resp = self._session.get(url, headers=self._get_headers(), timeout=self.timeout)
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed
                last_http_code = resp.status_code

                if resp.status_code in (503, 403):
                    last_status = "blocked"
                    last_error = f"HTTP {resp.status_code} — blocked"
                    continue
                elif resp.status_code != 200:
                    last_status = "failed"
                    last_error = f"HTTP {resp.status_code}"
                    continue

                if "captcha" in resp.text.lower():
                    last_status = "blocked"
                    last_error = "Amazon CAPTCHA detected"
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                results = self._parse_results(soup, term, component_config, price_floor)
                all_results.extend(results)
                if results:
                    last_status = "success"

            except Exception as e:
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed
                last_status = "failed"
                last_error = str(e)
                logger.error(f"Amazon error for {term}: {e}")

            time.sleep(self.delay + random.uniform(1.0, 3.0))

        if all_results:
            return ScrapeOutcome(status="success", results=all_results, response_time_ms=total_ms)
        return ScrapeOutcome(status=last_status, error_message=last_error, http_status_code=last_http_code, response_time_ms=total_ms)

    def _parse_results(self, soup, term, component_config, price_floor):
        results = []
        try:
            items = soup.select('[data-component-type="s-search-result"]')
            for item in items[:20]:
                try:
                    product_name = ""
                    for sel in ["h2 a span", ".a-size-medium.a-color-base.a-text-normal",
                                ".a-size-base-plus.a-color-base.a-text-normal", "h2 span"]:
                        el = item.select_one(sel)
                        if el:
                            product_name = el.get_text(strip=True)
                            if len(product_name) > 10:
                                break

                    if not product_name or len(product_name) < 5:
                        continue

                    if not passes_keyword_filter(product_name, component_config):
                        logger.debug(f"Amazon filtered: {product_name[:50]}")
                        continue

                    # ASIN-based URL (most reliable)
                    prod_url = ""
                    asin = item.get("data-asin", "")
                    if asin:
                        prod_url = f"https://www.amazon.com/dp/{asin}"
                    else:
                        link_el = item.select_one("h2 a") or item.select_one("a.a-link-normal")
                        if link_el:
                            prod_url = clean_amazon_url(link_el.get("href", ""))

                    price_val = None
                    for selector in [".a-price .a-offscreen", ".a-price-whole",
                                     "[class*='price'] .a-offscreen", ".a-color-price"]:
                        price_el = item.select_one(selector)
                        if price_el:
                            price_text = price_el.get_text(strip=True)
                            m = re.search(r"[\d,]+\.?\d*", price_text.replace("$", ""))
                            if m:
                                price_val = float(m.group().replace(",", ""))
                                break

                    if price_val is None or price_val <= 0 or price_val < price_floor:
                        if price_val is not None:
                            logger.debug(f"Amazon price floor filtered: {product_name[:40]} at ${price_val}")
                        continue

                    results.append(ScrapeResult(
                        product_name=product_name,
                        price=price_val,
                        retailer="amazon",
                        url=prod_url,
                        source="amazon"
                    ))
                except Exception as e:
                    logger.debug(f"Amazon item parse error: {e}")
                    continue
        except Exception as e:
            logger.error(f"Amazon parse error: {e}")
        return results
