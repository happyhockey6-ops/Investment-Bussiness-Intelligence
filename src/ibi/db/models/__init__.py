"""Import every model so `Base.metadata` is complete for Alembic autogenerate
and for `Base.metadata.create_all()` in tests."""

from ibi.db.models.alert import AlertRecord
from ibi.db.models.decision import DecisionRecord
from ibi.db.models.entity import EntityRecord
from ibi.db.models.event import EventRecord
from ibi.db.models.evidence import ClaimRecord, EvidenceRecord
from ibi.db.models.filing import FilingRecord
from ibi.db.models.financial import FinancialDataPointRecord
from ibi.db.models.market import MarketDataBarRecord
from ibi.db.models.metric_result import (
    FactQuarantineRecord,
    MetricGenerationRecord,
    MetricResultInputRecord,
    MetricResultRecord,
    MetricVersionActivationRecord,
)
from ibi.db.models.observation import ObservationRecord
from ibi.db.models.prediction import OutcomeRecord, PredictionRecord
from ibi.db.models.provider import ProviderCallRecord
from ibi.db.models.research import ResearchRecord
from ibi.db.models.source import SourceDocumentRecord
from ibi.db.models.thesis import ScenarioRecord, ThesisRecord

__all__ = [
    "AlertRecord",
    "DecisionRecord",
    "EntityRecord",
    "EventRecord",
    "ClaimRecord",
    "EvidenceRecord",
    "FactQuarantineRecord",
    "FilingRecord",
    "FinancialDataPointRecord",
    "MarketDataBarRecord",
    "MetricGenerationRecord",
    "MetricResultInputRecord",
    "MetricResultRecord",
    "MetricVersionActivationRecord",
    "ObservationRecord",
    "OutcomeRecord",
    "PredictionRecord",
    "ProviderCallRecord",
    "ResearchRecord",
    "SourceDocumentRecord",
    "ScenarioRecord",
    "ThesisRecord",
]
