from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from quality.models import IntegrityStatus, RunKind, RunRecord, RunStatus
from quality.storage import (
    append_jsonl,
    ensure_quality_dirs,
    read_jsonl,
    write_json_atomic,
)


def _run_record() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        trigger="local",
        environment="china-test",
        start_time=datetime(2026, 7, 30, 8, 0, tzinfo=UTC),
        status=RunStatus.FINISHED,
        integrity_status=IntegrityStatus.COMPLETE,
        run_kind=RunKind.NORMAL,
    )


def test_write_json_atomic_creates_parent_and_writes_utf8_model(tmp_path):
    target = tmp_path / "nested" / "run.json"

    result = write_json_atomic(target, _run_record())
    loaded = json.loads(target.read_text(encoding="utf-8"))

    assert result == target
    assert loaded["schema_version"] == "quality.v2"
    assert loaded["run_id"] == "run-1"
    assert target.read_bytes().endswith(b"\n")


def test_write_json_atomic_replaces_existing_file(tmp_path):
    target = tmp_path / "run.json"
    target.write_text('{"old":true}\n', encoding="utf-8")

    write_json_atomic(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_write_json_atomic_keeps_old_file_and_cleans_temp_on_replace_failure(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "run.json"
    target.write_text('{"old":true}\n', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr("quality.storage.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_json_atomic(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".run.json.*.tmp")) == []


def test_append_and_read_jsonl_preserve_one_record_per_line(tmp_path):
    target = tmp_path / "shards" / "cases.jsonl"

    append_jsonl(target, {"index": 1, "message": "line one\nline two"})
    append_jsonl(target, _run_record())

    physical_lines = target.read_text(encoding="utf-8").splitlines()
    records = read_jsonl(target)

    assert len(physical_lines) == 2
    assert records[0] == {"index": 1, "message": "line one\nline two"}
    assert records[1]["run_id"] == "run-1"


def test_read_jsonl_ignores_empty_lines_and_reports_invalid_line(tmp_path):
    target = tmp_path / "records.jsonl"
    target.write_text('{"ok":1}\n\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"records\.jsonl:3"):
        read_jsonl(target)


def test_ensure_quality_dirs_only_creates_on_explicit_call(tmp_path):
    root = tmp_path / "reports" / "quality"

    assert not root.exists()

    layout = ensure_quality_dirs(root)

    assert layout.root == root
    assert layout.shards == root / "shards"
    assert layout.merged == root / "merged"
    assert all(path.is_dir() for path in (layout.root, layout.shards, layout.merged))


def test_storage_rejects_unsupported_objects(tmp_path):
    target = tmp_path / "unsupported.json"

    with pytest.raises(TypeError, match="not JSON serializable"):
        write_json_atomic(target, object())

    assert not target.exists()


def test_storage_rejects_non_finite_json_numbers(tmp_path):
    target = tmp_path / "non-finite.json"

    with pytest.raises(ValueError, match="JSON compliant"):
        write_json_atomic(target, {"value": float("nan")})

    assert not target.exists()
