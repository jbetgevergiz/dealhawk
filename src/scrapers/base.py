from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

@dataclass
class ScrapeResult:
    product_name: str
    price: float
    retailer: str
    url: str
    source: str

@dataclass
class ScrapeOutcome:
    status: str  # 'success', 'failed', 'timeout', 'no_results', 'parse_error', 'blocked'
    results: List[ScrapeResult] = field(default_factory=list)
    error_message: Optional[str] = None
    http_status_code: Optional[int] = None
    response_time_ms: int = 0
    attempted_at: datetime = field(default_factory=datetime.now)

class BaseScraper(ABC):
    @abstractmethod
    def scrape(self, component_config: dict) -> ScrapeOutcome:
        pass
