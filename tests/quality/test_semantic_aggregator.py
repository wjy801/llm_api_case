from __future__ import annotations

import json

import requests

from common.base_request import BaseRequest
from quality.aggregator import QualityMergeRequest, merge_quality_run
from quality.models import IntegrityStatus
from quality.semantic_aggregator import SemanticMergeRequest, merge_semantic_run


class DummyConfig:
    base_url = "https://example.com"
    api_key = "secret"
    timeout = 5


def _response() -> requests.Response:
    response = requests.Response()
    response.status_code = 200
    response.url = "https://example.com/v1/items"
    response._content = json.dumps({"usage": {"prompt_tokens": 1}}).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def _write_runtime_facts(semantic_runtime) -> None:
    client = BaseRequest(config=DummyConfig())
    client.session.request = lambda method, url, **kwargs: _response()  # type: ignore[method-assign]
    client.get("/v1/items", _attach_log=False)
    merge_quality_run(
        QualityMergeRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )


def test_semantic_merge_links_p0_evidence_and_writes_manifest(semantic_runtime):
    _write_runtime_facts(semantic_runtime)

    result = merge_semantic_run(
        SemanticMergeRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.integrity_status is IntegrityStatus.COMPLETE
    assert result.operations == result.request_groups == 1
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["p0_evidence"]["manifest_sha256"]
    assert manifest["p0_evidence"]["request_metrics_sha256"]
    assert manifest["output_hashes"]["operations"]


def test_semantic_merge_rejects_tampered_p0_request_metrics(semantic_runtime):
    _write_runtime_facts(semantic_runtime)
    request_metrics = semantic_runtime.output_dir / "merged" / "request-metrics.jsonl"
    request_metrics.write_text(request_metrics.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = merge_semantic_run(
        SemanticMergeRequest(
            run_id=semantic_runtime.run_context.run_id,
            output_dir=semantic_runtime.output_dir,
        )
    )

    assert result.integrity_status is IntegrityStatus.FAILED
    assert any(
        issue.code == "p0_request_metrics_hash_mismatch"
        for issue in result.integrity_issues
    )
