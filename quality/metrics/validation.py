from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from pydantic import BaseModel

from .contracts import MetricsSourceError, MetricsSources
from .request_event import event_transport_outcome


_T = TypeVar("_T", bound=BaseModel)


def validate_source_relationships(run_id: str, sources: MetricsSources) -> None:
    events = unique_index(
        sources.requests,
        lambda item: item.request_event_id,
        "request_event_duplicate",
    )
    groups = unique_index(
        sources.groups,
        lambda item: item.request_group_id,
        "request_group_duplicate",
    )
    sessions = unique_index(
        sources.sessions,
        lambda item: item.polling_session_id,
        "polling_session_duplicate",
    )
    operations = unique_index(
        sources.operations,
        lambda item: item.operation_id,
        "operation_duplicate",
    )
    for record in (
        *sources.requests,
        *sources.groups,
        *sources.sessions,
        *sources.operations,
    ):
        if record.run_id != run_id:
            raise_source_error(
                "foreign_run_record", "a source record belongs to another run"
            )

    event_owner: dict[str, str] = {}
    for group in sources.groups:
        operation = operations.get(group.operation_id)
        if operation is None:
            raise_source_error(
                "group_operation_missing",
                "request group references a missing operation",
                group.request_group_id,
            )
        if group.request_group_id not in operation.request_group_ids:
            raise_source_error(
                "group_operation_reference_mismatch",
                "request group is absent from its operation references",
                group.request_group_id,
            )
        require_identity_match(group, operation, group.request_group_id)
        if group.final_request_event_id != group.attempt_event_ids[-1]:
            raise_source_error(
                "final_request_event_invalid",
                "request group final event is not its last attempt",
                group.request_group_id,
            )
        for expected_index, event_id in enumerate(group.attempt_event_ids, start=1):
            previous = event_owner.setdefault(event_id, group.request_group_id)
            if previous != group.request_group_id:
                raise_source_error(
                    "request_event_multiple_groups",
                    "one request event belongs to multiple request groups",
                    event_id,
                )
            event = events.get(event_id)
            if event is None:
                raise_source_error(
                    "request_event_missing",
                    "request group references a missing request event",
                    event_id,
                )
            require_identity_match(group, event, event_id)
            if event.attempt_index != expected_index:
                raise_source_error(
                    "attempt_index_sequence_invalid",
                    "request attempt indexes are not continuous",
                    group.request_group_id,
                )
            if (
                event.interface_id != group.interface_id
                or event.protocol is not group.protocol
                or event.method != group.method
                or event.url_template != group.url_template
            ):
                raise_source_error(
                    "group_event_interface_mismatch",
                    "request group and event interface identity differ",
                    event_id,
                )
        first_event = events[group.attempt_event_ids[0]]
        final_event = events[group.final_request_event_id]
        if (
            group.first_transport_outcome is not event_transport_outcome(first_event)
            or group.final_transport_outcome is not event_transport_outcome(final_event)
            or group.first_status_code != first_event.status_code
            or group.final_status_code != final_event.status_code
        ):
            raise_source_error(
                "group_outcome_evidence_mismatch",
                "request group first/final outcome differs from its event evidence",
                group.request_group_id,
            )

    group_owner: dict[str, str] = {}
    session_owner: dict[str, str] = {}
    usage_owner: dict[str, str] = {}
    for operation in sources.operations:
        operation_event_ids: set[str] = set()
        for group_id in operation.request_group_ids:
            previous = group_owner.setdefault(group_id, operation.operation_id)
            if previous != operation.operation_id:
                raise_source_error(
                    "request_group_multiple_operations",
                    "one request group belongs to multiple operations",
                    group_id,
                )
            group = groups.get(group_id)
            if group is None:
                raise_source_error(
                    "operation_group_missing",
                    "operation references a missing request group",
                    group_id,
                )
            if group.operation_id != operation.operation_id:
                raise_source_error(
                    "operation_group_identity_mismatch",
                    "operation and request group ids disagree",
                    group_id,
                )
            require_identity_match(operation, group, group_id)
            operation_event_ids.update(group.attempt_event_ids)
        for session_id in operation.polling_session_ids:
            previous = session_owner.setdefault(session_id, operation.operation_id)
            if previous != operation.operation_id:
                raise_source_error(
                    "polling_session_multiple_operations",
                    "one polling session belongs to multiple operations",
                    session_id,
                )
            session = sessions.get(session_id)
            if session is None:
                raise_source_error(
                    "operation_polling_session_missing",
                    "operation references a missing polling session",
                    session_id,
                )
            if session.operation_id != operation.operation_id:
                raise_source_error(
                    "operation_polling_identity_mismatch",
                    "operation and polling session ids disagree",
                    session_id,
                )
            require_identity_match(operation, session, session_id)
        usage_ids = (
            *operation.usage.source_request_event_ids,
            *operation.usage.missing_request_event_ids,
        )
        if len(set(usage_ids)) != len(usage_ids):
            raise_source_error(
                "usage_evidence_overlap",
                "operation usage known/missing evidence overlaps",
                operation.operation_id,
            )
        for event_id in usage_ids:
            if event_id not in operation_event_ids or event_id not in events:
                raise_source_error(
                    "usage_event_outside_operation",
                    "usage evidence is outside its operation",
                    event_id,
                )
            previous = usage_owner.setdefault(event_id, operation.operation_id)
            if previous != operation.operation_id:
                raise_source_error(
                    "usage_event_multiple_operations",
                    "one usage event belongs to multiple operations",
                    event_id,
                )

    if set(groups) != set(group_owner):
        orphan = sorted(set(groups) - set(group_owner))[0]
        raise_source_error(
            "request_group_unassigned",
            "a semantic request group has no owning operation",
            orphan,
        )

    for session in sources.sessions:
        operation = operations.get(session.operation_id)
        if (
            operation is None
            or session.polling_session_id not in operation.polling_session_ids
        ):
            raise_source_error(
                "polling_session_unassigned",
                "a polling session has no owning operation",
                session.polling_session_id,
            )
        for group_id in session.request_group_ids:
            group = groups.get(group_id)
            if group is None:
                raise_source_error(
                    "polling_group_missing",
                    "polling session references a missing request group",
                    group_id,
                )
            if (
                group.polling_session_id != session.polling_session_id
                or group.operation_id != session.operation_id
            ):
                raise_source_error(
                    "polling_group_identity_mismatch",
                    "polling session and request group identity differ",
                    group_id,
                )
            require_identity_match(session, group, group_id)


def require_manifest(
    manifest: dict[str, Any],
    *,
    run_id: str,
    status: str,
    versions: dict[str, str],
    code_prefix: str,
) -> None:
    if manifest.get("run_id") != run_id:
        raise_source_error(
            f"{code_prefix}_manifest_run_id_mismatch",
            f"{code_prefix} manifest belongs to a different run",
        )
    if manifest.get("status") != status:
        raise_source_error(
            f"{code_prefix}_manifest_not_complete",
            f"{code_prefix} manifest is not committed",
        )
    for field, expected in versions.items():
        if manifest.get(field) != expected:
            raise_source_error(
                f"{code_prefix}_{field}_unsupported",
                f"{code_prefix} manifest uses an unsupported {field}",
            )


def validated_output_hash(
    path: Path,
    expected: object,
    actual: str,
    code_prefix: str,
) -> str:
    if not isinstance(expected, str) or expected != actual:
        raise_source_error(
            f"{code_prefix}_hash_mismatch",
            f"source hash does not match its manifest for {path.name}",
            path.name,
        )
    return actual


def unique_index(
    values: Sequence[_T],
    key: Callable[[_T], str],
    code: str,
) -> dict[str, _T]:
    result: dict[str, _T] = {}
    for value in values:
        identity = key(value)
        if identity in result:
            raise_source_error(code, "source contains a duplicate identity", identity)
        result[identity] = value
    return result


def require_identity_match(left: Any, right: Any, related_id: str) -> None:
    if (
        left.run_id != right.run_id
        or left.case_id != right.case_id
        or left.invocation_id != right.invocation_id
    ):
        raise_source_error(
            "semantic_identity_mismatch",
            "related semantic facts have different run/case/invocation identity",
            related_id,
        )


def raise_source_error(
    code: str, summary: str, related_id: str | None = None
) -> NoReturn:
    raise MetricsSourceError(code, summary, related_id)


def relative_artifact_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        raise_source_error(
            "source_path_outside_output", "source path is outside output_dir"
        )
