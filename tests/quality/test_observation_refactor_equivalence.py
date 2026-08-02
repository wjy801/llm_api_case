from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

from quality.observation_report import (
    P1ObservationRequest,
    generate_p1_observation_report,
)
from quality.observation_report import service


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "observation_refactor"


def copy_observation_sources(tmp_path: Path) -> Path:
    output_dir = tmp_path / "quality"
    shutil.copytree(FIXTURE_DIR / "sources", output_dir)
    return output_dir


def freeze_observation_time(monkeypatch) -> None:
    payload = json.loads((FIXTURE_DIR / "expected-report.json").read_text(encoding="utf-8"))
    generated_at = datetime.fromisoformat(payload["generated_at"])

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return generated_at

    monkeypatch.setattr(service, "datetime", FrozenDateTime)


def test_refactor_preserves_frozen_json_markdown_and_manifest(tmp_path, monkeypatch):
    output_dir = copy_observation_sources(tmp_path)
    freeze_observation_time(monkeypatch)

    result = generate_p1_observation_report(
        P1ObservationRequest(run_id="run-semantic", output_dir=output_dir)
    )

    assert result.json_path.read_bytes() == (FIXTURE_DIR / "expected-report.json").read_bytes()
    assert result.markdown_path.read_bytes() == (FIXTURE_DIR / "expected-report.md").read_bytes()
    assert result.manifest_path.read_bytes() == (FIXTURE_DIR / "expected-manifest.json").read_bytes()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["output_hashes"]["json"] == hashlib.sha256(
        result.json_path.read_bytes()
    ).hexdigest()
    assert manifest["output_hashes"]["markdown"] == hashlib.sha256(
        result.markdown_path.read_bytes()
    ).hexdigest()


def test_repeated_fixture_replay_preserves_business_sections_and_attention(
    tmp_path, monkeypatch
):
    output_dir = copy_observation_sources(tmp_path)
    freeze_observation_time(monkeypatch)
    request = P1ObservationRequest(run_id="run-semantic", output_dir=output_dir)

    first = generate_p1_observation_report(request)
    first_payload = first.json_path.read_bytes()
    first_markdown = first.markdown_path.read_bytes()
    second = generate_p1_observation_report(request)

    assert second.json_path.read_bytes() == first_payload
    assert second.markdown_path.read_bytes() == first_markdown
    assert first.report is not None and second.report is not None
    assert first.report.attention_items == second.report.attention_items
    assert first.report.display_windows == second.report.display_windows
