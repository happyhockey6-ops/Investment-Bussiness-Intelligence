"""Real SEC reconciliation: ingest the covered companies, build metrics, compare.

    python -m ibi.validation.sec_reconciliation --database-url <dedicated URL>

Requirements (checked before any request is sent):
- IBI_SEC_USER_AGENT names the organization and a real contact email;
- network access to data.sec.gov;
- a dedicated database whose name contains "validation" or "test". It is
  migrated to head and written to. Never point this at a production database.

Reports, per company: every line item for the reference fiscal year against
`expected_fundamentals.json`, the coverage of the fundamentals page (how many
cells are values, restated, conflicting or missing), and the status of the
derived metrics. Exits non-zero on any mismatch, ingestion failure or
missing reference value.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from ibi.config import get_settings
from ibi.data_engine.sec_edgar import ingest
from ibi.data_engine.sec_edgar.fixed_universe import FIXED_UNIVERSE
from ibi.db import base as db
from ibi.financial_engine import results_store as rs
from ibi.research.fundamentals import CellStatus, annual_fundamentals

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIRED_REFERENCE_ITEMS = (
    "revenue",
    "cost_of_revenue",
    "operating_income",
    "net_income",
    "eps_diluted",
    "operating_cash_flow",
    "capital_expenditures",
    "cash",
    "total_assets",
)
REQUIRED_DERIVED_METRICS = ("ibi:gross_margin", "ibi:free_cash_flow")


def load_expected() -> dict:
    text = resources.files("ibi.validation").joinpath("expected_fundamentals.json").read_text()
    return json.loads(text, parse_float=Decimal)


def guard_database(url: str) -> None:
    name = make_url(url).database or ""
    if "validation" not in name and "test" not in name:
        raise SystemExit(
            f"Refusing to use database {name!r}: the name must contain 'validation' or 'test'."
        )


def reconcile(session: Session, expected: dict) -> tuple[list[str], bool]:
    lines: list[str] = []
    ok = True
    references = expected.get("companies", {})
    if not isinstance(references, dict):
        return ["FAIL companies: reference mapping is missing or invalid"], False

    def expected_number(raw):
        try:
            value = Decimal(str(raw))
            return value if value.is_finite() else None
        except InvalidOperation:
            return None

    for entity in FIXED_UNIVERSE:
        ref = references.get(entity.ticker or "")
        f = annual_fundamentals(session, entity.entity_id, entity.cik)
        statuses = Counter(c.status.value for row in f.rows.values() for c in row.values())
        lines.append(
            f"\n== {entity.ticker} {entity.canonical_name}: {len(f.years)} fiscal "
            f"years; cells {dict(statuses)}"
        )
        for metric, cells in f.derived.items():
            lines.append(f"   {metric}: {dict(Counter(c.status for c in cells.values()))}")
        if not f.has_data:
            ok = False
            lines.append("   FAIL company: no annual data")
        if not isinstance(ref, dict):
            ok = False
            lines.append("   FAIL company: missing required reference")
            continue
        if ref.get("verified") is not True:
            ok = False
            lines.append("   FAIL reference: not verified against the filing")
        if not ref.get("filing"):
            ok = False
            lines.append("   FAIL reference: missing source filing")
        try:
            end = date.fromisoformat(ref["fiscal_year_end"])
        except (KeyError, TypeError, ValueError):
            ok = False
            lines.append("   FAIL reference: missing or invalid fiscal_year_end")
            continue
        values = ref.get("values", {})
        if not isinstance(values, dict):
            values = {}
        keys = set(REQUIRED_REFERENCE_ITEMS) | set(values)
        for key in sorted(keys):
            if key not in values or (want := expected_number(values[key])) is None:
                ok = False
                lines.append(f"   FAIL {key}: missing or invalid required reference value")
                continue
            cell = f.rows.get(key, {}).get(end)
            if cell is None or cell.status not in (CellStatus.VALUE, CellStatus.RESTATED):
                ok = False
                status = "absent" if cell is None else cell.status.value
                lines.append(f"   FAIL {key}: expected {want}, page shows {status}")
            elif cell.value != want:
                ok = False
                lines.append(
                    f"   FAIL {key}: expected {want}, got {cell.value} "
                    f"({cell.source.tag}, {cell.source.accession_number})"
                )
            else:
                lines.append(
                    f"   ok   {key}: {cell.value} ({cell.source.tag}, "
                    f"{cell.source.accession_number}, {cell.status.value})"
                )
        derived_refs = ref.get("derived", {})
        if not isinstance(derived_refs, dict):
            derived_refs = {}
        for metric in REQUIRED_DERIVED_METRICS:
            want = expected_number(derived_refs.get(metric))
            cell = f.derived.get(metric, {}).get(end)
            if want is None:
                ok = False
                lines.append(f"   FAIL {metric}: missing or invalid required derived reference")
            if cell is None or cell.status != "value" or cell.value is None:
                ok = False
                status = "absent" if cell is None else cell.status
                lines.append(f"   FAIL {metric}: required metric unavailable ({status})")
                continue
            if want is not None and cell.value != want:
                ok = False
                lines.append(f"   FAIL {metric}: expected {want}, got {cell.value}")
            elif want is not None:
                lines.append(f"   ok   {metric}: {cell.value}")
            if (
                cell.result_id is None
                or cell.generation_id is None
                or cell.versions is None
                or cell.basis_accession is None
                or cell.effective_from is None
                or not cell.evidence
            ):
                ok = False
                lines.append(f"   FAIL {metric}: calculation provenance is incomplete")
    return lines, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--raw-data-dir", default="data/validation-raw")
    args = parser.parse_args(argv)

    guard_database(args.database_url)
    base = get_settings()
    base.require_sec_user_agent()  # fail before any network request
    settings = base.model_copy(
        update={"database_url": args.database_url, "raw_data_dir": args.raw_data_dir}
    )

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", args.database_url)
    # alembic/env.py takes its URL from Settings, i.e. from IBI_DATABASE_URL.
    os.environ["IBI_DATABASE_URL"] = args.database_url
    get_settings.cache_clear()
    command.upgrade(config, "head")

    engine = create_engine(args.database_url)
    db._SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    summary = ingest.run_ingestion(settings)
    failures = [r for r in summary.results if r.status != "success"]
    for r in summary.results:
        print(
            f"ingest {r.entity_id}: {r.status} facts_written={r.facts_written} "
            f"skipped={r.facts_skipped_duplicate} {r.error_type or ''} {r.error or ''}"
        )

    with Session(engine) as s, s.begin():
        for entity in FIXED_UNIVERSE:
            for metric in REQUIRED_DERIVED_METRICS:
                pair = rs.VersionPair("1", "1")
                outcome = rs.build_generation(s, entity.entity_id, metric, pair)
                print(
                    f"build {entity.ticker} {metric}: {outcome.status} rows={outcome.rows_written}"
                )

    with Session(engine) as s:
        lines, ok = reconcile(s, load_expected())
    print("\n".join(lines))
    passed = ok and not failures
    print("\nRESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
