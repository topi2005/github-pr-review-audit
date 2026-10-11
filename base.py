"""Integration-agnostic building blocks shared by every data source.

To add a new integration (e.g. Jira):
  1. subclass BaseExtractor   -> how to list items and fetch one item
  2. subclass BaseTransformer -> how to turn one raw item into a report row
  3. register a builder in main.py's INTEGRATIONS dict
Authentication, retries, rate limiting, file output and CSV reporting are
handled here and are not re-implemented per integration.
"""
import time
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd
import requests

from utils import get_logger, load_json, save_json

log = get_logger("base")


class ApiClient:
    """HTTP session with auth, retry/backoff, and rate-limit handling."""

    RETRY_STATUSES = (502, 503, 504)

    def __init__(self, headers: dict | None = None, auth: tuple | None = None,
                 retries: int = 4, timeout: int = 30):
        self.session = requests.Session()
        if headers:
            self.session.headers.update(headers)
        if auth:
            self.session.auth = auth  # e.g. (email, api_token) for Jira basic auth
        self.retries = retries
        self.timeout = timeout

    @staticmethod
    def _rate_limit_wait(resp: requests.Response) -> int | None:
        """Seconds to wait if this response is a rate limit, else None."""
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            return int(retry_after)
        if resp.status_code == 429:
            return 60
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(resp.headers.get("X-RateLimit-Reset", 0))
            return max(reset - int(time.time()), 1)
        return None  # e.g. a plain 403 permission error: do not retry

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        for attempt in range(1, self.retries + 1):
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)

            if resp.status_code == 401:
                raise PermissionError("Authentication failed (401): check your credentials.")

            if resp.status_code in self.RETRY_STATUSES:
                time.sleep(2 ** attempt)
                continue

            if resp.status_code in (403, 429):
                wait = self._rate_limit_wait(resp)
                if wait is not None:
                    log.warning("Rate limited, sleeping %ss", wait)
                    time.sleep(wait)
                    continue

            resp.raise_for_status()
            return resp

        raise RuntimeError(f"Request to {url} failed after {self.retries} attempts.")


class BaseExtractor(ABC):
    """Template for 'list the items, then fetch each one' extraction."""

    name = "base"  # used for output folder names

    def __init__(self, max_items: int = 200, output_root: str = "data"):
        self.max_items = max_items
        self.output_root = output_root
        self.output_dir = f"{output_root}/extracted/{self.name}"

    @abstractmethod
    def fetch_item_ids(self) -> list:
        """Return up to self.max_items item identifiers."""

    @abstractmethod
    def fetch_item_detail(self, item_id) -> dict:
        """Return the raw record for one item."""

    def run(self) -> list[dict]:
        ids = self.fetch_item_ids()
        save_json(ids, f"{self.output_dir}/ids.json")

        details = []
        for i, item_id in enumerate(ids, 1):
            details.append(self.fetch_item_detail(item_id))
            if i % 25 == 0:
                log.info("[%s] fetched %d/%d", self.name, i, len(ids))

        save_json(details, f"{self.output_dir}/details.json")
        return details

    def load_cached(self) -> list[dict]:
        return load_json(f"{self.output_dir}/details.json")


class BaseTransformer(ABC):
    """Turns raw records into flat report rows."""

    sort_field: str | None = None
    sort_reverse = True

    @abstractmethod
    def transform_item(self, raw: dict) -> dict:
        """Return one flat report row for one raw record."""

    def transform_all(self, items: list[dict]) -> list[dict]:
        rows = [self.transform_item(item) for item in items if item]
        if self.sort_field:
            rows.sort(key=lambda r: r[self.sort_field], reverse=self.sort_reverse)
        return rows


class Pipeline:
    """extract -> transform -> report, identical for every integration."""

    def __init__(self, extractor: BaseExtractor, transformer: BaseTransformer,
                 report_name: str):
        self.extractor = extractor
        self.transformer = transformer
        self.report_name = report_name
        self.root = extractor.output_root

    def run(self, skip_extract: bool = False) -> list[dict]:
        if skip_extract:
            details = self.extractor.load_cached()
            log.info("Loaded %d cached items", len(details))
        else:
            details = self.extractor.run()

        rows = self.transformer.transform_all(details)
        save_json(rows, f"{self.root}/transformed/{self.extractor.name}/rows.json")

        report_path = Path(self.root) / "reports" / self.report_name
        report_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(report_path, index=False)
        log.info("Report written: %d rows -> %s", len(rows), report_path)
        return rows
