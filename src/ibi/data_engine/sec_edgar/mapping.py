"""Pure normalization: raw SEC JSON -> typed rows.

No I/O, no AI, fully deterministic — every function here takes already
-fetched JSON (a `dict`, as returned by `client.SecHttpClient.get_json`)
and returns typed dataclasses. This is the layer responsible for the
approved identity model and point-in-time rule; `connector.py`/`ingest.py`
are just wiring around it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from ibi.core.epistemics import EpistemicLabel
from ibi.data_engine.sec_edgar.availability import Availability, compute_known_available_at

SOURCE_NAME = "sec_edgar"


def cik_to_entity_id(cik: int | str) -> str:
    """`320193` -> `"CIK0000320193"` — see ARCHITECTURE.md on entity id format."""
    return f"CIK{int(cik):010d}"


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _parse_acceptance_datetime(s: str | None) -> datetime | None:
    """SEC's format is e.g. `"2025-10-31T10:01:26.000Z"` — always UTC."""
    if not s:
        return None
    # datetime.fromisoformat doesn't accept a bare "Z" suffix before Python 3.11's
    # improvements; normalize explicitly rather than assume interpreter version behavior.
    normalized = s.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True, slots=True)
class FilingRow:
    entity_id: str
    source: str
    accession_number: str
    form_type: str
    filing_date: date
    period_of_report: date | None
    primary_document: str | None
    acceptance_date_time: datetime | None
    availability: Availability


def parse_submissions(entity_id: str, raw: dict) -> list[FilingRow]:
    """Parse `submissions.json`'s `filings.recent` block into `FilingRow`s.

    Only the "recent" window is read in Phase 1 — older, paginated filing
    history (`filings.files[]`) is not fetched; a filing outside the recent
    window simply won't have a `FilingRow` (its facts, if any, still get
    ingested via `parse_companyfacts`, falling back to the day-conservative
    availability rule since no acceptanceDateTime will be found for them).
    """
    recent = raw.get("filings", {}).get("recent", {})
    accession_numbers = recent.get("accessionNumber", [])
    n = len(accession_numbers)
    acceptance_raw = recent.get("acceptanceDateTime", [None] * n)
    report_dates_raw = recent.get("reportDate", [""] * n)
    primary_documents = recent.get("primaryDocument", [None] * n)

    rows: list[FilingRow] = []
    for i in range(n):
        filing_date = _parse_date(recent["filingDate"][i])
        acceptance = _parse_acceptance_datetime(acceptance_raw[i])
        report_date_raw = report_dates_raw[i]
        rows.append(
            FilingRow(
                entity_id=entity_id,
                source=SOURCE_NAME,
                accession_number=accession_numbers[i],
                form_type=recent["form"][i],
                filing_date=filing_date,
                period_of_report=_parse_date(report_date_raw) if report_date_raw else None,
                primary_document=primary_documents[i] or None,
                acceptance_date_time=acceptance,
                availability=compute_known_available_at(filing_date, acceptance),
            )
        )
    return rows


@dataclass(frozen=True, slots=True)
class FactRow:
    entity_id: str
    metric_id: str  # "<taxonomy>:<tag>"
    unit: str
    start_date: date | None
    end_date: date
    value: Decimal
    accession_number: str
    epistemic_label: EpistemicLabel
    availability: Availability


def parse_companyfacts(
    entity_id: str, raw: dict, filing_lookup: dict[str, FilingRow] | None = None
) -> list[FactRow]:
    """Parse `companyfacts.json` into `FactRow`s.

    `filing_lookup` (accession_number -> `FilingRow`, from `parse_submissions`
    for the same entity) lets a fact use its filing's acceptance-timestamp
    -based availability when known; a fact whose accession isn't in the
    lookup (outside the fetched "recent" window) falls back to the
    day-conservative rule computed from the fact's own `filed` date — this
    is the correct, designed degradation, not a bug.

    Every fact is labeled FACT: it was directly observed in a primary
    regulatory filing (SEC, PRIMARY_REGULATORY tier) — see
    docs/epistemic_model.md. A restated value arrives under a different
    accession_number as an additional row; nothing is ever overwritten.
    """
    filing_lookup = filing_lookup or {}
    rows: list[FactRow] = []
    for taxonomy, tags in raw.get("facts", {}).items():
        for tag, tag_data in tags.items():
            for unit, entries in tag_data.get("units", {}).items():
                for entry in entries:
                    accession_number = entry["accn"]
                    filing = filing_lookup.get(accession_number)
                    if filing is not None:
                        availability = filing.availability
                    else:
                        filed_date = _parse_date(entry["filed"])
                        availability = compute_known_available_at(filed_date, None)

                    start_raw = entry.get("start")
                    rows.append(
                        FactRow(
                            entity_id=entity_id,
                            metric_id=f"{taxonomy}:{tag}",
                            unit=unit,
                            start_date=_parse_date(start_raw) if start_raw else None,
                            end_date=_parse_date(entry["end"]),
                            value=Decimal(str(entry["val"])),
                            accession_number=accession_number,
                            epistemic_label=EpistemicLabel.FACT,
                            availability=availability,
                        )
                    )
    return rows
