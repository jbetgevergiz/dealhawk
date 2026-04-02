import time
import random
import logging
import requests
from bs4 import BeautifulSoup
from .base import BaseScraper, ScrapeResult, ScrapeOutcome

logger = logging.getLogger(__name__)

GPU_CATEGORIES = {"video-card"}
GPU_MAX_PRICE = 3000
NON_GPU_MAX_PRICE = 10000

class PCPartPickerScraper(BaseScraper):
    BASE_URL = "https://pcpartpicker.com/products/{category}/?search={term}"

    def __init__(self, config: dict):
        self.user_agents = config.get("scraping", {}).get("user_agents", [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ])
        self.timeout = config.get("scraping", {}).get("request_timeout_seconds", 30)
        self.delay = config.get("scraping", {}).get("request_delay_seconds", 3)

    def _get_headers(self):
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

    def _validate_price(self, price: float, category: str) -> bool:
        if price <= 0:
            return False
        max_price = GPU_MAX_PRICE if category in GPU_CATEGORIES else NON_GPU_MAX_PRICE
        return price <= max_price

    def scrape(self, component_config: dict) -> ScrapeOutcome:
        category = component_config.get("category", "")
        search_terms = component_config.get("search_terms", [])
        if not search_terms:
            return ScrapeOutcome(status="failed", error_message="No search terms provided")

        all_results = []
        last_status = "no_results"
        last_error = None
        last_http_code = None
        total_ms = 0

        for term in search_terms:
            url = self.BASE_URL.format(category=category, term=requests.utils.quote(term))
            start = time.time()
            try:
                resp = requests.get(url, headers=self._get_headers(), timeout=self.timeout)
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed

                if resp.status_code in (403, 429):
                    logger.warning(f"PCPartPicker blocked: {resp.status_code} for {term}")
                    return ScrapeOutcome(
                        status="blocked",
                        http_status_code=resp.status_code,
                        response_time_ms=elapsed,
                        error_message=f"HTTP {resp.status_code}"
                    )

                if resp.status_code != 200:
                    last_status = "failed"
                    last_http_code = resp.status_code
                    last_error = f"HTTP {resp.status_code}"
                    continue

                soup = BeautifulSoup(resp.text, "lxml")
                results = self._parse_results(soup, category, url)
                all_results.extend(results)

                if results:
                    last_status = "success"

            except requests.exceptions.Timeout:
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed
                last_status = "timeout"
                last_error = "Request timed out"
                logger.warning(f"PCPartPicker timeout for {term}")
            except Exception as e:
                elapsed = int((time.time() - start) * 1000)
                total_ms += elapsed
                last_status = "parse_error"
                last_error = str(e)
                logger.error(f"PCPartPicker error for {term}: {e}")

            time.sleep(self.delay)

        if all_results:
            # Filter valid prices
            valid = [r for r in all_results if self._validate_price(r.price, category)]
            if valid:
                return ScrapeOutcome(
                    status="success",
                    results=valid,
                    response_time_ms=total_ms
                )
            return ScrapeOutcome(status="no_results", error_message="All prices failed validation", response_time_ms=total_ms)

        return ScrapeOutcome(
            status=last_status,
            error_message=last_error,
            http_status_code=last_http_code,
            response_time_ms=total_ms
        )

    def _parse_results(self, soup: BeautifulSoup, category: str, page_url: str) -> list:
        results = []
        try:
            product_rows = soup.select("ul.productList li.tr__product")
            if not product_rows:
                product_rows = soup.select("li[class*='tr__product']")

            for row in product_rows:
                try:
                    # Get product name
                    name_el = row.select_one("p.td__name a") or row.select_one("a.td__name")
                    if not name_el:
                        name_el = row.select_one("p.td__name") or row.select_one("[class*='td__name']")
                    if not name_el:
                        continue
                    product_name = name_el.get_text(strip=True)

                    # Get price and merchant
                    price_cells = row.select("td.td__price, [class*='td__price']")
                    for pcell in price_cells:
                        merchant_el = pcell.select_one("a[href*='amazon'], a[href*='newegg']")
                        price_el = pcell.select_one("span.price-current, span[class*='price']")
                        if not price_el:
                            price_el = pcell

                        price_text = price_el.get_text(strip=True)
                        price_val = self._parse_price(price_text)
                        if price_val is None:
                            continue

                        if merchant_el:
                            href = merchant_el.get("href", "")
                            if "amazon" in href:
                                retailer = "amazon"
                            elif "newegg" in href:
                                retailer = "newegg"
                            else:
                                continue
                            prod_url = href
                        else:
                            continue

                        results.append(ScrapeResult(
                            product_name=product_name,
                            price=price_val,
                            retailer=retailer,
                            url=prod_url,
                            source="pcpartpicker"
                        ))
                except Exception as e:
                    logger.debug(f"Row parse error: {e}")
                    continue
        except Exception as e:
            logger.error(f"Parse error: {e}")
        return results

    def _parse_price(self, text: str):
        try:
            cleaned = text.replace("$", "").replace(",", "").strip()
            # Take first number found
            import re
            m = re.search(r"[\d]+\.?[\d]*", cleaned)
            if m:
                return float(m.group())
        except Exception:
            pass
        return None
