"""`SecEdgarConnector`: the `SourceConnector` implementation for SEC EDGAR.

Fetches the exact SEC response bytes and wraps them with provenance and a
SHA-256 digest. Nothing is parsed here: `ingest.py` archives the bytes
first and only then decodes them, so even an unparseable response is kept
as evidence. Normalization into typed rows is `mapping.py`'s job.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ibi.core.epistemics import Provenance, SourceTier
from ibi.core.types import Unknown
from ibi.data_engine.interfaces import RawRecord, SourceConnector
from ibi.data_engine.sec_edgar.client import SecHttpClient
from ibi.data_engine.sec_edgar.source_bytes import sha256_hex
from ibi.logging import get_logger, log_context

logger = get_logger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/{cik}.json"


def _cik_param(entity_id: str) -> str:
    """`"CIK0000320193"` -> `"CIK0000320193"` (already the exact path segment
    SEC's URLs expect — see mapping.cik_to_entity_id)."""
    if not entity_id.startswith("CIK") or len(entity_id) != 13:
        raise ValueError(f"Expected an entity_id like 'CIK0000320193', got {entity_id!r}")
    return entity_id


class SecEdgarConnector(SourceConnector):
    def __init__(self, client: SecHttpClient) -> None:
        self._client = client

    @property
    def source_name(self) -> str:
        return "sec_edgar"

    def fetch(self, entity_id: str) -> list[RawRecord]:
        cik_param = _cik_param(entity_id)
        records: list[RawRecord] = []

        for label, url_template in (
            ("submissions", SUBMISSIONS_URL),
            ("companyfacts", COMPANYFACTS_URL),
        ):
            url = url_template.format(cik=cik_param)
            retrieved_at = datetime.now(UTC)
            result = self._client.get_bytes(url)
            if isinstance(result, Unknown):
                logger.info(
                    f"sec_edgar {label} unavailable for entity",
                    extra=log_context(
                        operation=f"data_engine.sec_edgar.fetch.{label}",
                        provider="sec_edgar",
                        entity_id=entity_id,
                    ),
                )
                continue
            records.append(
                RawRecord(
                    entity_id=entity_id,
                    payload={
                        "kind": label, "url": url,
                        "raw_bytes": result, "sha256": sha256_hex(result),
                    },
                    provenance=Provenance(
                        source="SEC EDGAR",
                        source_tier=SourceTier.PRIMARY_REGULATORY,
                        retrieval_date=retrieved_at,
                        source_url=url,
                    ),
                )
            )
        return records
