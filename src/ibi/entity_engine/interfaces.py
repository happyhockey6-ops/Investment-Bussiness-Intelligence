"""The `EntityRepository` contract.

Every other engine references entities by `entity_id` (a stable string,
never a mutable name); this is the one place that id is minted and looked up.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class EntityKind(StrEnum):
    COMPANY = "company"
    PERSON = "person"
    GOVERNMENT_BODY = "government_body"
    REGULATOR = "regulator"
    ASSET = "asset"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Entity:
    entity_id: str
    kind: EntityKind
    canonical_name: str
    aliases: tuple[str, ...] = ()


class EntityRepository(ABC):
    """Not implemented in Phase 0."""

    @abstractmethod
    def resolve(self, name_or_alias: str) -> Entity | None: ...

    @abstractmethod
    def get(self, entity_id: str) -> Entity | None: ...
