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
# Useful for filtering out irrelevant products in broad categories
REQUIRED_KEYWORDS: dict = {
    # Example:
    # "memory": ["64gb", "64 gb"],
    # "power-supply": ["1000w", "1000 w"],
}

# Keywords that must NOT appear in product name
BLACKLIST_KEYWORDS: dict = {
    # Example:
    # "case": ["fan", "fans", "case fan"],
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

    # Must-have keywords
    required = REQUIRED_KEYWORDS.get(category, [])
    if required and not any(kw in name_lower for kw in required):
        return False

    # Blacklist keywords
    blacklist = BLACKLIST_KEYWORDS.get(category, [])
    if any(kw in name_lower for kw in blacklist):
        return False

    return True


class NeweggScraper(BaseScraper):
    BASE_URL = "https://www.newegg.com/p/pl?d={term}"

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
            logger.info("NeweggScraper: using curl_cffi (Chrome TLS impersonation)")
        except ImportError:
            self._session = requests.Session()
            self._use_cffi = False
            logger.info("NeweggScraper: using requests (curl_cffi not installed)")

    def _get_headers(self):
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://www.newegg.com/",
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

        for term in search_terms[:2]:
            url = self.BASE_URL.format(term=requests.utils.quote(term))
            start = time.time()
            try:
                resp = self._session.get(url, headers=self._get_headers(), timeout=self.timeout)
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed
                last_http_code = resp.status_code

                if resp.status_code in (403, 429):
                    last_status = "blocked"
                    last_error = f"HTTP {resp.status_code} — blocked"
                    continue
                elif resp.status_code != 200:
                    last_status = "failed"
                    last_error = f"HTTP {resp.status_code}"
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
                logger.error(f"Newegg error for {term}: {e}")

            time.sleep(self.delay + random.uniform(0.5, 1.5))

        # Deduplicate
        seen = set()
        unique_results = []
        for r in all_results:
            key = (r.product_name[:50], r.price)
            if key not in seen:
                seen.add(key)
                unique_results.append(r)

        if unique_results:
            return ScrapeOutcome(status="success", results=unique_results, response_time_ms=total_ms)
        return ScrapeOutcome(status=last_status, error_message=last_error, http_status_code=last_http_code, response_time_ms=total_ms)

    def _parse_results(self, soup, term, component_config, price_floor):
        results = []
        try:
            items = (soup.select(".item-cell") or
                     soup.select(".goods-name-wrapper") or
                     soup.select("[class*='item-container']"))

            for item in items[:20]:
                try:
                    name_el = (item.select_one(".item-title") or
                               item.select_one("a.item-title") or
                               item.select_one("[class*='item-title']"))
                    if not name_el:
                        continue
                    product_name = name_el.get_text(strip=True)

                    if not passes_keyword_filter(product_name, component_config):
                        logger.debug(f"Newegg filtered: {product_name[:50]}")
                        continue

                    link_el = item.find("a")
                    prod_url = ""
                    if link_el:
                        href = link_el.get("href", "")
                        prod_url = "https://www.newegg.com" + href if href.startswith("/") else href

                    price_el = (item.select_one(".price-current") or
                                item.select_one("[class*='price-current']"))
                    if not price_el:
                        continue
                    price_text = price_el.get_text(strip=True)
                    m = re.search(r"[\d,]+\.?\d*", price_text.replace("$", ""))
                    if not m:
                        continue
                    price_val = float(m.group().replace(",", ""))

                    if price_val <= 0 or price_val < price_floor:
                        logger.debug(f"Newegg price floor filtered: {product_name[:40]} at ${price_val}")
                        continue

                    results.append(ScrapeResult(
                        product_name=product_name,
                        price=price_val,
                        retailer="newegg",
                        url=prod_url,
                        source="newegg"
                    ))
                except Exception as e:
                    logger.debug(f"Newegg item parse error: {e}")
                    continue
        except Exception as e:
            logger.error(f"Newegg parse error: {e}")
        return results
