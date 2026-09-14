"""The epistemic model: how the platform labels what it believes and why.

Core architectural principle (see ARCHITECTURE.md): source data, deterministic
computation, and AI interpretation are different kinds of things, and every
piece of information the platform stores must say which kind it is. An AI
model may produce an INFERENCE or a PREDICTION; it must never produce a
FACT, and a CALCULATION must come from a deterministic formula in
`financial_engine`, never from an LLM.

This module defines that vocabulary and the `Evidence` record that carries
it. It intentionally does not implement storage, retrieval, or scoring of
evidence — that is `knowledge_graph` and `ai_research_engine`'s job in a
later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class EpistemicLabel(StrEnum):
    """What kind of claim a piece of information is, epistemically.

    Ordering below is roughly "how directly traceable to reality", not
    "how important" — a well-labeled ASSUMPTION is more useful than an
    unlabeled FACT, because the label tells a reader how much weight it can
    bear.
    """

    FACT = "fact"
    """Directly observed/verifiable in a primary source (e.g. a 10-K line item)."""

    REPORTED = "reported"
    """Stated by a source (e.g. a news article, a company press release) but
    not independently verified against a primary source."""

    CALCULATION = "calculation"
    """Deterministically derived from FACT/REPORTED inputs by a documented,
    versioned formula. Must be reproducible from its inputs. Never produced
    by an AI model — see `financial_engine`."""

    INFERENCE = "inference"
    """A conclusion drawn (by a human or an AI) from evidence, where the
    conclusion is not itself directly observed."""

    ASSUMPTION = "assumption"
    """An input taken as given for the purpose of a calculation or thesis,
    explicitly flagged as unverified."""

    PREDICTION = "prediction"
    """A statement about a future, not-yet-observable state."""

    SPECULATION = "speculation"
    """A low-confidence hypothesis offered for consideration, explicitly not
    a prediction the system stands behind."""


class SourceTier(StrEnum):
    """A coarse reliability tier for where a piece of information came from.

    Deliberately coarse in Phase 0 — a full source-reliability model belongs
    to `ai_research_engine` / `knowledge_graph` once real sources exist.
    """

    PRIMARY_REGULATORY = "primary_regulatory"
    """E.g. SEC filings, official regulatory disclosures."""

    PRIMARY_COMPANY = "primary_company"
    """E.g. company press releases, investor decks, earnings calls."""

    ESTABLISHED_MEDIA = "established_media"
    SECONDARY_ANALYSIS = "secondary_analysis"
    """E.g. sell-side research, third-party analysis."""

    UNVERIFIED = "unverified"
    """Social media, forums, or any source with no established reliability record."""

    AI_GENERATED = "ai_generated"
    """The content itself was produced by an AI model, not a human source.
    Must never be conflated with a primary source when computing confidence."""


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a piece of information came from and when the system learned it.

    `publication_date` / `observation_date` describe the world; `retrieval_date`
    describes the system. Keeping them separate is what makes point-in-time
    backtesting possible later (see `backtesting_engine`): a fact retrieved
    today about an observation dated a year ago must not leak into a
    simulation of "what was knowable a year ago".
    """

    source: str
    source_tier: SourceTier
    retrieval_date: datetime
    publication_date: date | None = None
    observation_date: date | None = None
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    """A single, traceable unit of information about an entity.

    This is the atomic record the eventual Knowledge Graph and AI Research
    Engine are built on. It is deliberately data-only (no behavior) in
    Phase 0; persistence lives in `db.models.evidence`, and scoring/synthesis
    are future-phase concerns.
    """

    entity_id: str
    claim: str
    epistemic_label: EpistemicLabel
    provenance: Provenance
    confidence: float | None = None  # 0.0-1.0; None means "not yet assessed"

    def __post_init__(self) -> None:
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence!r}")
        if self.epistemic_label == EpistemicLabel.FACT and self.provenance.source_tier in (
            SourceTier.AI_GENERATED,
            SourceTier.UNVERIFIED,
        ):
            raise ValueError(
                "A claim from an AI-generated or unverified source cannot be labeled FACT; "
                "use REPORTED, INFERENCE, or SPECULATION instead."
            )
