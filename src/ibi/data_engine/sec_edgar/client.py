"""Minimal SEC EDGAR HTTP client.

Built on the standard library (`urllib.request`) rather than adding a new
dependency (e.g. httpx/requests) — SEC's JSON APIs are plain GETs, which
stdlib handles fine, and Phase 1's charter is to keep the dependency list
minimal (see pyproject.toml).

Every request carries the configured `User-Agent` (required by SEC's fair
-access policy — see SECURITY.md) and a bounded timeout, retries transient
failures with backoff, and never retries a non-429 4xx (a bad CIK won't fix
itself). Rate limiting is a fixed delay between requests — Phase 1's fixed
2-entity universe needs at most a handful of requests per run, far under
SEC's ~10 req/s ceiling, so a conservative fixed delay is simpler and safer
than a token-bucket implementation this scale doesn't need.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ibi.core.errors import ProviderError
from ibi.core.types import Unknown
from ibi.logging import get_logger, log_context

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 10
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 0.5  # far under SEC's ~10 req/s ceiling


@dataclass(frozen=True, slots=True)
class SecHttpClientConfig:
    user_agent: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS
    min_request_interval_seconds: float = _DEFAULT_MIN_REQUEST_INTERVAL_SECONDS


class SecHttpClient:
    """Fetches a JSON document from a `data.sec.gov`/`www.sec.gov` URL."""

    def __init__(self, config: SecHttpClientConfig) -> None:
        if not config.user_agent:
            raise ProviderError("SecHttpClient requires a non-empty user_agent.")
        self._config = config
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._config.min_request_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def get_json(self, url: str) -> dict | Unknown:
        """Return the parsed JSON body, or `Unknown` for a 404 (a normal,
        expected outcome — not every CIK has data at every endpoint).
        Raises `ProviderError` for anything else that isn't recoverable
        after retrying.
        """
        request = urllib.request.Request(url, headers={"User-Agent": self._config.user_agent})

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            self._throttle()
            self._last_request_at = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as resp:
                    body = resp.read()
                logger.info(
                    "sec_edgar http request succeeded",
                    extra=log_context(
                        operation="data_engine.sec_edgar.http_get", provider="sec_edgar"
                    ),
                )
                return json.loads(body)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return Unknown(reason=f"SEC returned 404 for {url}")
                if e.code == 429 or e.code >= 500:
                    last_error = e
                    logger.warning(
                        "sec_edgar http request retrying",
                        extra=log_context(
                            operation="data_engine.sec_edgar.http_get", provider="sec_edgar"
                        ),
                    )
                    time.sleep(2**attempt)
                    continue
                raise ProviderError(f"SEC EDGAR request failed ({e.code}) for {url}: {e}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = e
                logger.warning(
                    "sec_edgar http request retrying after network error",
                    extra=log_context(
                        operation="data_engine.sec_edgar.http_get", provider="sec_edgar"
                    ),
                )
                time.sleep(2**attempt)
                continue

        raise ProviderError(
            f"SEC EDGAR request failed after {self._config.max_attempts} attempts "
            f"for {url}: {last_error}"
        )
