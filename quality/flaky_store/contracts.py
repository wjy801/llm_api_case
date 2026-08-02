from __future__ import annotations

from dataclasses import dataclass

from quality.flaky_models import (
    FlakyStateRecord,
    FlakyTransitionRecord,
    GovernanceResolution,
)


DEFAULT_BUSY_TIMEOUT_MS = 5000


class FlakyStoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StoreInitialization:
    schema_version: int
    quick_check: str
    migration_applied: bool
    backup_created: bool


@dataclass(frozen=True)
class StoreImportOutcome:
    imported: bool
    inserted_count: int
    initialization: StoreInitialization


@dataclass(frozen=True)
class ProjectionPlan:
    state: FlakyStateRecord
    transitions: tuple[FlakyTransitionRecord, ...]
    changed: bool
    close_governance_id: str | None = None
    governance_resolution: GovernanceResolution | None = None


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def required_text(value: str, name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    return stripped
