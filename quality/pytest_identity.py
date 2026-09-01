from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import unicodedata
from typing import Any

from quality.identifiers import build_case_id, build_param_hash, normalize_nodeid


@dataclass(frozen=True)
class PytestItemIdentity:
    nodeid: str
    case_id: str
    param_hash: str
    parameter_id: str | None
    normalized_case_path: str


def build_pytest_item_identity(
    item: Any,
    repository_root: str | Path,
) -> PytestItemIdentity:
    normalized = normalize_nodeid(str(item.nodeid))
    callspec = getattr(item, "callspec", None)
    parameter_value = None
    if callspec is not None:
        parameter_value = {
            "parameter_id": normalized.parameter_id,
            "params": callspec.params,
        }
    return PytestItemIdentity(
        nodeid=str(item.nodeid),
        case_id=build_case_id(str(item.nodeid)),
        param_hash=build_param_hash(parameter_value),
        parameter_id=normalized.parameter_id,
        normalized_case_path=normalize_item_path(item, repository_root),
    )


def normalize_item_path(item: Any, repository_root: str | Path) -> str:
    item_path = getattr(item, "path", None)
    if item_path is None:
        item_path = getattr(item, "fspath", None)
    if item_path is None:
        raise ValueError("pytest item does not expose a file path")
    return _resolved_repository_path(Path(str(item_path)), repository_root)


def normalize_case_path(case_id: str, repository_root: str | Path) -> str:
    value = unicodedata.normalize("NFC", str(case_id)).split("::", 1)[0]
    value = value.replace("\\", "/")
    if not value or value.startswith(("/", "//")):
        raise ValueError("case_id path must be repository-relative")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("case_id path contains an invalid segment")
    pure = PurePosixPath(value)
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("case_id path contains an invalid segment")
    if ":" in parts[0]:
        raise ValueError("case_id path must not contain a drive")
    return _resolved_repository_path(Path(*parts), repository_root)


def normalize_directory_prefix(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", str(value)).replace("\\", "/")
    if normalized.startswith(("/", "//")):
        raise ValueError("path prefix must be repository-relative")
    raw_parts = normalized.split("/")
    if raw_parts and raw_parts[-1] == "":
        raw_parts.pop()
    if any(part == "" for part in raw_parts):
        raise ValueError("path prefix contains an empty segment")
    parts = tuple(raw_parts)
    if not parts or any(part in {".", ".."} for part in parts) or ":" in parts[0]:
        raise ValueError("path prefix is invalid")
    return parts


def path_has_directory_prefix(path: str, prefix: str) -> bool:
    path_parts = tuple(PurePosixPath(path).parts)
    prefix_parts = normalize_directory_prefix(prefix)
    return path_parts[: len(prefix_parts)] == prefix_parts


def path_is_in_policy_scope(
    path: str,
    *,
    include_prefixes: tuple[str, ...],
    exclude_prefixes: tuple[str, ...],
) -> bool:
    if any(path_has_directory_prefix(path, prefix) for prefix in exclude_prefixes):
        return False
    return any(path_has_directory_prefix(path, prefix) for prefix in include_prefixes)


def _resolved_repository_path(path: Path, repository_root: str | Path) -> str:
    root = Path(repository_root).resolve(strict=True)
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("case path must resolve to a file")
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("case path resolves outside the repository") from error
    normalized = unicodedata.normalize("NFC", relative.as_posix())
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("normalized case path is invalid")
    return normalized


__all__ = (
    "PytestItemIdentity",
    "build_pytest_item_identity",
    "normalize_case_path",
    "normalize_directory_prefix",
    "normalize_item_path",
    "path_has_directory_prefix",
    "path_is_in_policy_scope",
)
