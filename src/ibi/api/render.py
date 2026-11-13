"""Server-rendered HTML: no JavaScript, no external assets, works on a phone.

Every value is escaped. Every reported number links to the SEC filing it
came from; restated and conflicting values are marked, never hidden.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape

from ibi.data_engine.sec_edgar.fixed_universe import FixedUniverseEntity
from ibi.research.fundamentals import (
    DERIVED_METRICS,
    LINE_ITEMS,
    Cell,
    CellStatus,
    DerivedCell,
    FilingNotice,
    Fundamentals,
    Source,
    Statement,
    filing_url,
)

_STYLE = """
:root{--bg:#fff;--fg:#14171a;--muted:#5b6670;--line:#e3e7ea;--accent:#1f5fbf;
--warn:#8a5a00;--warnbg:#fff4dc;--bad:#a1262b;--badbg:#fde8e8;--head:#f6f8f9}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--fg:#e8eaed;--muted:#9aa3ab;
--line:#2b3036;--accent:#7fb0ff;--warn:#f3c16b;--warnbg:#3a2e14;--bad:#ff8f8f;
--badbg:#3a1c1e;--head:#1a1d21}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}a{color:var(--accent)}
h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1.05rem;margin:1.4rem 0 .4rem}
.muted{color:var(--muted)}.small{font-size:.82rem}
form{display:flex;gap:8px;margin:12px 0}input[type=search]{flex:1;min-width:0;
padding:10px 12px;font-size:16px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--fg)}button{padding:10px 16px;font-size:16px;
border:0;border-radius:8px;background:var(--accent);color:#fff}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);
border-radius:8px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:var(--bg);
white-space:normal;min-width:8.5rem;max-width:40vw}.unit{display:block;font-weight:400}
thead th{background:var(--head)}tbody tr:last-child td{border-bottom:0}
.restated{background:var(--warnbg);color:var(--warn)}
.conflict{background:var(--badbg);color:var(--bad)}
sup a{text-decoration:none;font-size:.7rem}ul.results{padding-left:1.1rem}
summary{cursor:pointer;color:var(--accent);white-space:normal}
details{text-align:left;white-space:normal;max-width:22rem;min-width:7rem}
details ul{padding-left:1.2rem;overflow-wrap:anywhere}
summary:focus-visible{outline:2px solid var(--accent)}
.unverified{background:var(--warnbg);color:var(--warn)}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<meta name=robots content='noindex,nofollow'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _search_form(q: str = "") -> str:
    return (
        "<form action='/' method=get role=search>"
        f"<input type=search name=q value='{escape(q, quote=True)}' "
        "placeholder='Ticker or company name' aria-label='Ticker or company name' "
        "autocapitalize=characters autocomplete=off>"
        "<button type=submit>Research</button></form>"
    )


def redirect_page(location: str) -> str:
    loc = escape(location, quote=True)
    return _page(
        "Redirecting",
        f"<meta http-equiv=refresh content='0;url={loc}'><p><a href='{loc}'>Continue</a></p>",
    )


def home_page(q: str, matches: list[FixedUniverseEntity]) -> str:
    items = "".join(
        f"<li><a href='/companies/{escape(e.ticker or e.entity_id, quote=True)}'>"
        f"{escape(e.ticker or '')}</a> — {escape(e.canonical_name)}</li>"
        for e in matches
    )
    found = (
        f"<ul class=results>{items}</ul>"
        if items
        else (f"<p class=muted>No covered company matches “{escape(q)}”.</p>")
    )
    return _page(
        "Trusty",
        (
            "<h1>Trusty</h1><p class=muted>Private investment research. Sourced, "
            "reproducible fundamentals from SEC filings.</p>"
            + _search_form(q)
            + "<h2>Covered companies</h2>"
            + found
        ),
    )


def format_value(value: Decimal, unit: str) -> str:
    if unit == "USD/shares":
        return f"{value:,.2f}"
    millions = value / Decimal(1_000_000)
    return f"{millions:,.0f}" if abs(millions) >= 10 else f"{millions:,.1f}"


def _source_title(s: Source, label: str) -> str:
    return (
        f"{label}: {s.value} ({s.tag}) — {s.form_type} accession {s.accession_number}, "
        f"public {s.known_available_at:%Y-%m-%d %H:%M} UTC"
    )


def _source_details(s: Source, label: str) -> str:
    return (
        f"<li>{escape(_source_title(s, label))}; fact {s.fact_id}; "
        f"precision {escape(s.availability_precision)} "
        f"<a href='{escape(s.url, quote=True)}' rel=noreferrer target=_blank>"
        "Source filing</a></li>"
    )


def _notice_details(notice: FilingNotice) -> str:
    return (
        f"<li>Non-reliance notice: {escape(notice.form_type)} "
        f"{escape(notice.accession_number)}, items {escape(notice.items or '')}, "
        f"public {notice.known_available_at:%Y-%m-%d %H:%M} UTC; "
        f"precision {escape(notice.availability_precision)} "
        f"<a href='{escape(notice.url, quote=True)}' rel=noreferrer target=_blank>"
        "Notice filing</a></li>"
    )


def _cell_html(cell: Cell, unit: str) -> str:
    if cell.status is CellStatus.MISSING and not cell.evidence:
        return (
            "<td class=muted>— <span class=small>"
            "Not reported in an eligible annual filing</span></td>"
        )
    css = ""
    if cell.status is CellStatus.CONFLICTING:
        css, display = " class=conflict", "conflicting"
    elif cell.status is CellStatus.UNVERIFIED_REVISION:
        css, display = " class=unverified", "unverified revision"
    elif cell.status is CellStatus.MISSING:
        display = "—"
    else:
        assert cell.source is not None and cell.value is not None
        display = escape(format_value(cell.value, unit))
        if cell.status is CellStatus.RESTATED:
            css, display = " class=restated", display + " R"
    entries = []
    seen = set()
    for label, sources in (
        ("Annual basis", (cell.source,) if cell.source else ()),
        ("Originally reported", (cell.original,) if cell.original else ()),
        ("Conflicting evidence", cell.conflicting),
        ("Supporting/revision evidence", cell.evidence),
        ("Historical disagreement (superseded)", cell.historical_conflicts),
    ):
        for source in sources:
            key = (label, source.fact_id)
            if key not in seen:
                entries.append(_source_details(source, label))
                seen.add(key)
    entries.extend(_notice_details(notice) for notice in cell.notices)
    reason = (
        f"<p>{escape(cell.reason_code or '')}: {escape(cell.detail)}</p>" if cell.detail else ""
    )
    details = (
        "<details><summary>Sources and evidence</summary>"
        + reason
        + "<ul>"
        + "".join(entries)
        + "</ul></details>"
    )
    return f"<td{css}>{display}{details}</td>"


def _derived_html(cell: DerivedCell | None, metric: str, cik: int) -> str:
    if cell is None:
        return "<td class=muted>— Not computed</td>"
    if cell.status != "value" or cell.value is None:
        display = f"<span class=muted>— {escape(cell.status)}</span>"
    else:
        display = (
            f"{cell.value * 100:,.1f}%"
            if metric == "ibi:gross_margin"
            else escape(format_value(cell.value, "USD"))
        )
    versions = cell.versions
    version_text = (
        f"formula {versions.calculation_version}, policy {versions.selection_policy_version}"
        if versions
        else "versions unavailable"
    )
    effective = cell.effective_from.isoformat() if cell.effective_from else "unavailable"
    metadata = (
        f"<p>Result {cell.result_id}; generation {cell.generation_id}; {escape(version_text)}; "
        f"basis {escape(cell.basis_accession or 'unavailable')}; "
        f"effective {escape(effective)}; "
        f"revision {escape(cell.revision_kind or 'none')}</p>"
    )
    evidence = "".join(
        f"<li>{escape(e.role)} / {escape(e.relation)}: {escape(e.metric_id)} = {e.value}; "
        f"fact {e.fact_id}; {escape(e.form_type or 'unknown form')}; "
        f"public {e.known_available_at:%Y-%m-%d %H:%M} UTC; "
        f"<a href='{escape(filing_url(cik, e.accession_number, None), quote=True)}' "
        f"rel=noreferrer target=_blank>{escape(e.accession_number)}</a></li>"
        for e in cell.evidence
    )
    reason = (
        f"<p>{escape(cell.reason_code or '')}: {escape(cell.detail)}</p>" if cell.detail else ""
    )
    return (
        f"<td>{display}<details><summary>Calculation and evidence</summary>"
        + metadata
        + reason
        + "<ul>"
        + evidence
        + "".join(_notice_details(n) for n in cell.notices)
        + "</ul></details></td>"
    )


_UNIT_NOTE = {"USD": "USD millions", "USD/shares": "USD per share", "shares": "millions"}
_STATEMENT_TITLE = {
    Statement.INCOME: "Income statement",
    Statement.CASH_FLOW: "Cash flow",
    Statement.BALANCE_SHEET: "Balance sheet (fiscal year-end)",
}


def company_page(entity: FixedUniverseEntity, f: Fundamentals) -> str:
    head = (
        f"<p class=small><a href='/'>← Search</a></p>"
        f"<h1>{escape(entity.canonical_name)} <span class=muted>"
        f"{escape(entity.ticker or '')}</span></h1>"
        f"<p class='muted small'>SEC CIK {entity.cik} · as of "
        f"{f.as_of:%Y-%m-%d %H:%M} UTC · statement v{escape(f.statement_version)}</p>"
    )
    if not f.has_data:
        return _page(
            entity.canonical_name,
            head
            + (
                "<p>No annual (10-K) figures are stored for this company yet. "
                "Run SEC ingestion first.</p>"
            ),
        )
    years = "".join(f"<th scope=col>{escape(y.label)}</th>" for y in f.years)
    sections = []
    for statement, title in _STATEMENT_TITLE.items():
        rows = "".join(
            f"<tr><th scope=row>{escape(item.label)} "
            f"<span class='muted small unit'>{escape(_UNIT_NOTE[item.unit])}</span></th>"
            + "".join(_cell_html(f.rows[item.key][y.end], item.unit) for y in f.years)
            + "</tr>"
            for item in LINE_ITEMS
            if item.statement is statement
        )
        sections.append(
            f"<h2>{title}</h2><div class=scroll><table><thead><tr><th scope=col>"
            f"</th>{years}</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    derived_rows = "".join(
        f"<tr><th scope=row>{escape(label)}"
        + (
            "<span class='muted small unit'>USD millions</span>"
            if metric == "ibi:free_cash_flow"
            else ""
        )
        + "</th>"
        + "".join(
            _derived_html(f.derived.get(metric, {}).get(y.end), metric, entity.cik) for y in f.years
        )
        + "</tr>"
        for metric, label in DERIVED_METRICS
    )
    sections.append(
        "<h2>Calculated metrics</h2><div class=scroll><table><thead><tr><th scope=col>"
        f"</th>{years}</tr></thead><tbody>{derived_rows}</tbody></table></div>"
    )
    legend = (
        "<p class='muted small'>Annual values come from eligible 10-K/10-K/A filings. "
        "Open Sources and evidence for exact values, tags, filing links and public dates. "
        "R marks a changed annual value with an unambiguous original. "
        "Conflicting and unverified revisions have no accepted value. "
        "Historical disagreements remain available separately. — means missing or uncertain; "
        "open the details for the reason. Non-reliance coverage: "
        f"{escape(f.non_reliance_coverage)}.</p>"
    )

    return _page(f"{entity.ticker} · Trusty", head + "".join(sections) + legend)
