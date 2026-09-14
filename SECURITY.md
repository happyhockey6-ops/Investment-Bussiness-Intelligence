# Security

## Secrets

- All configuration, including secrets, flows through `ibi.config.Settings`
  (`pydantic-settings`). No module reads `os.environ` directly — this keeps
  every required credential visible in one place.
- `.env` is gitignored; `.env.example` documents every variable with no
  real values (see `.gitignore` / `.env.example`).
- API keys (`ANTHROPIC_API_KEY`, `IBI_MARKET_DATA_API_KEY`) are typed as
  `pydantic.SecretStr`, which prevents them from appearing in plain text in
  `repr()`/`str()` output (e.g. if a `Settings` object is accidentally
  logged).
- `Settings.require_anthropic_api_key()` fails loudly at the call site that
  needs the key rather than the codebase silently working without AI
  capability — a missing/misconfigured key is an explicit `RuntimeError`,
  not a silent no-op.
- No credential has a default value that looks like a real one; missing
  configuration fails validation at startup (via pydantic) rather than
  proceeding with a guessed value.

## Logging

`ibi.logging` structures every log line as JSON or a readable console
format, with a defined set of standard context fields (`operation`,
`provider`, `model`, `data_version`, `decision_id`, `entity_id`,
`correlation_id`) — see `STANDARD_CONTEXT_FIELDS`. Callers must not pass
secret values into `log_context()`; the module does not scrub payloads for
secrets, so this is a code-review responsibility, not an automated one, in
Phase 0.

## Trust boundaries

- **Source data is untrusted at the epistemic level.** Everything ingested
  is tagged with an `ibi.core.epistemics.EpistemicLabel` and a
  `SourceTier`; nothing is assumed correct because it came from an external
  feed.
- **AI-generated content is untrusted, structurally.**
  `Evidence.__post_init__` raises if a `SourceTier.AI_GENERATED` or
  `SourceTier.UNVERIFIED` claim is labeled `FACT` — this is a type-level
  guard against the model's own output being laundered into "fact" status.
- **Future risk, documented now: prompt injection from ingested external
  content.** Once `ai_research_engine` and `data_engine` are implemented,
  any text retrieved from an external source (filings, news, web content)
  and passed into an `AIProvider` prompt is potentially adversarial —
  instructions embedded in a scraped document could attempt to manipulate
  the model. Phase 0 does not yet have a research pipeline to protect, but
  the architecture anticipates this: retrieved content must be clearly
  delimited from system/instruction text in any future prompt-construction
  code in `ai_research_engine`, and AI output must never be trusted to
  self-report its own reliability.

## Input validation

- Configuration is validated via `pydantic` (type coercion, `Literal`
  constraints on enum-like fields, a custom validator rejecting a negative
  AI budget).
- Domain dataclasses validate their own invariants in `__post_init__`
  (e.g. `Evidence` confidence must be in `[0, 1]`; `Thesis` must have
  exactly one bear/base/bull scenario) rather than trusting callers.

## Dependency hygiene

Phase 0 intentionally uses a small dependency set: `pydantic`,
`pydantic-settings`, `SQLAlchemy`, `alembic`, `psycopg`, plus `pytest` and
`ruff` for development. No AI SDK is a runtime dependency yet — it will be
added when `ClaudeProvider.complete` is actually implemented in a later
phase, not before.

## Known limitations (Phase 0)

- No automated secret-scanning is configured (e.g. a pre-commit hook or CI
  check for accidentally committed keys). Recommended before any real
  credential is used in this repository.
- No rate limiting, retry/backoff, or circuit-breaking exists for provider
  calls — there are no live provider call sites yet to need it.
- No authentication/authorization model exists — Phase 0 has no
  externally-facing service (`dashboard` is interfaces only).
- The AI monthly budget ceiling (`IBI_AI_MONTHLY_BUDGET_USD`) is captured in
  configuration but not enforced (`router.MonthlyBudgetTracker.check()` is a
  no-op) — do not rely on it as a real spend control yet.
- No database-level `CHECK` constraints exist for value ranges
  (e.g. confidence in `[0, 1]`) or enum-value membership — that validation
  currently lives only in the Python dataclasses (`Evidence`, `Scenario`,
  etc.) and is bypassed by any write that doesn't go through them (raw SQL,
  a future non-Python client). See docs/data_architecture.md. Deferred
  intentionally, not an oversight — see DECISIONS.md.

## Remediation audit findings (this pass)

A dedicated secret-pattern scan (`sk-ant-`, `sk-proj-`, AWS-style access
key ids, PEM private key headers, hardcoded `password=` literals) was run
across every tracked/trackable file in the repository (excluding `.venv`)
and found nothing. `.env` does not exist as a real file in this repository
— only `.env.example`, which contains no values. `git check-ignore`
confirmed `.env`, `.venv`, `src/ibi.egg-info`, `.pytest_cache`, and
`.ruff_cache` are all correctly ignored.
