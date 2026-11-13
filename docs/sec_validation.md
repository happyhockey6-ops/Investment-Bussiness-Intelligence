# Real SEC validation

Checks that Trusty's stored facts and research page match what companies
actually reported, using live SEC data. Run it whenever ingestion, the
selection policy or the fundamentals rules change.

## Requirements

1. **SEC contact.** SEC requires every request to identify the requester.
   Set `IBI_SEC_USER_AGENT` to the organization name and a monitored email,
   e.g. `Trusty Investment Research ops@your-domain.com`. `.env.example`
   ships with `<CONTACT_EMAIL>`; ingestion refuses to start while it is
   still there, or when no email is present (`Settings.require_sec_user_agent`).
2. **Network access to `data.sec.gov`.** In a Claude Code cloud session,
   the environment's network policy must allow `data.sec.gov` (and
   `www.sec.gov` for filing links). The `Investment-Bussiness-Intelligence`
   cloud environment allows both; the `Default` environment does not
   (the proxy answers `403` to the `CONNECT`). Locally, no change is needed.
3. **A dedicated database** whose name contains `validation` or `test`.
   The procedure migrates it to head and writes to it. Never use a
   production database.

## Run

```bash
export IBI_SEC_USER_AGENT="Trusty Investment Research you@your-domain.com"
createdb ibi_sec_validation
python -m ibi.validation.sec_reconciliation \
  --database-url postgresql+psycopg://<role>@localhost:5432/ibi_sec_validation
```

It ingests the covered companies (`fixed_universe.py`) at SEC's 2 requests
per second, builds `ibi:gross_margin` and `ibi:free_cash_flow`, and prints:

- per company, how many page cells are values, restated, conflicting or
  missing, and the status mix of each calculated metric;
- for the reference fiscal year in
  `src/ibi/validation/expected_fundamentals.json`, every line item as
  `ok` or `FAIL` with the XBRL tag and accession used.

It exits `0` only when every covered company ingested successfully and has
annual data, a complete independently verified reference, all nine required
reported line items, and matching available gross margin and free cash flow.
Both calculations must include result/generation IDs, formula and selection
policy versions, basis accession, effective date and supporting evidence.
An empty reference map, missing company, missing metric, uncertainty or
unverified reference makes the run fail, even if other values match.

## First run

The reference values are marked `"verified": false`: they were transcribed
for this check and have not been confirmed against the filings yet. On the
first run, open each filing from its link in the research page and confirm
every reference value and derived-metric input, then set `"verified": true`
in a pull request. The shipped derived references are arithmetic expectations
from the same unverified reported inputs, not independent confirmation.
The current reference file therefore intentionally cannot produce a passing
reconciliation. Any
`FAIL` is a finding to investigate (wrong tag mapping, a restatement, or a
wrong reference value), never something to paper over by editing the
expected value without checking the filing.

## Research statement version 2

The page and API resolve each exact annual period from eligible 10-K/10-K/A
facts available at the requested `as_of` time. Resolve the latest annual
filing timestamp first: disagreeing tags or filings at that timestamp have
no accepted value. Simultaneous disagreements in later nonannual filings
are also conflicts. Accession ordering only selects a representative after
all values agree. Disagreements in older filing epochs remain in
`historical_conflicts` and do not invalidate newer consistent reports.

Later divergent nonannual evidence is an unverified revision, never an
annual basis. Item 4.02 non-reliance notices at or after the basis timestamp
withhold both duration and instant values for prior periods. A later eligible
annual filing can establish a new basis; notices do not ban all subsequent
annual filings. Equal timestamps cannot establish event ordering and remain
uncertain. This conservative tie rule also withholds displayed calculations
while retaining their stored provenance; it does not change formula/policy
v1 or rewrite stored results. Non-reliance coverage remains explicitly
`submissions_recent_window_only`; it is not a complete historical notice scan.

The `long_term_debt` API key means **noncurrent long-term debt, excluding
current maturities**, from `us-gaap:LongTermDebtNoncurrent` only. Aggregate
`us-gaap:LongTermDebt` is not an alias or fallback. A period lacking the
selected tag is missing, preserving comparability across periods. This row
is not a total-debt measure.

Calculated metrics are read from the results store, never recomputed by the
page. The API retains every field supplied by `ResolvedMetric`: result ID,
generation ID, formula/policy version pair, basis accession, effective time,
revision kind and evidence. Uncertainty retains its full reason, reason code
and available evidence. The existing reader's uncertainty types do not supply
result/generation IDs or basis metadata; those fields remain null rather than
being invented. Statement version 2 identifies these display/selection rules
separately from the stored formula and policy versions.

Native expandable details expose exact source values, conflicts, historical
disagreements, notices and calculation evidence using touch or keyboard.
Calculated free cash flow is explicitly labeled **USD millions**; the API
continues to return exact base-unit decimal strings (`unit: "USD"`).
