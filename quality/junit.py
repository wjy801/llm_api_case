from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from quality.models import CaseStatus
from quality.redaction import redact_quality_value


QUALITY_CASE_ID_PROPERTY = "quality_case_id"
QUALITY_INVOCATION_ID_PROPERTY = "quality_invocation_id"

_LOCATION_PATTERN = re.compile(r"([A-Za-z]:)?[^:\n\r]+\.py:\d+")
_ERROR_TYPE_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Timeout))\b")
_MAX_MESSAGE_LENGTH = 500


@dataclass(frozen=True)
class JUnitCaseEvidence:
    junit_path: Path
    classname: str
    name: str
    status: CaseStatus
    case_id: str | None
    invocation_id: str | None
    error_type: str | None
    message: str | None
    assert_location: str | None
    duration_seconds: float


def parse_junit_file(path: str | Path) -> list[JUnitCaseEvidence]:
    junit_path = Path(path)
    root = ET.parse(junit_path).getroot()
    cases: list[JUnitCaseEvidence] = []
    for testcase in root.iter():
        if _strip_namespace(testcase.tag) != "testcase":
            continue
        cases.append(_parse_testcase(junit_path, testcase))
    return cases


def _parse_testcase(junit_path: Path, testcase: ET.Element) -> JUnitCaseEvidence:
    properties = _properties(testcase)
    status, evidence_element = _status_and_evidence(testcase)
    message = _message(evidence_element)
    return JUnitCaseEvidence(
        junit_path=junit_path,
        classname=str(testcase.attrib.get("classname") or ""),
        name=str(testcase.attrib.get("name") or ""),
        status=status,
        case_id=properties.get(QUALITY_CASE_ID_PROPERTY),
        invocation_id=properties.get(QUALITY_INVOCATION_ID_PROPERTY),
        error_type=_error_type(evidence_element, message),
        message=message,
        assert_location=_assert_location(evidence_element),
        duration_seconds=_duration_seconds(testcase),
    )


def _properties(testcase: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for child in testcase:
        if _strip_namespace(child.tag) != "properties":
            continue
        for property_element in child:
            if _strip_namespace(property_element.tag) != "property":
                continue
            name = property_element.attrib.get("name")
            value = property_element.attrib.get("value")
            if name and value:
                result[str(name)] = str(value)
    return result


def _status_and_evidence(testcase: ET.Element) -> tuple[CaseStatus, ET.Element | None]:
    skipped: ET.Element | None = None
    for child in testcase:
        tag = _strip_namespace(child.tag)
        if tag == "error":
            return CaseStatus.ERROR, child
        if tag == "failure":
            return CaseStatus.FAILED, child
        if tag == "skipped":
            skipped = child
    if skipped is not None:
        return CaseStatus.SKIPPED, skipped
    return CaseStatus.PASSED, None


def _message(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    values = [
        str(element.attrib.get("message") or ""),
        str(element.text or ""),
    ]
    message = "\n".join(value for value in values if value.strip()).strip()
    if not message:
        return None
    redacted = redact_quality_value(message, remove_url_query=True)
    text = str(redacted).strip()
    if len(text) > _MAX_MESSAGE_LENGTH:
        text = f"{text[:_MAX_MESSAGE_LENGTH]}...<truncated>"
    return text


def _error_type(element: ET.Element | None, message: str | None) -> str | None:
    if element is None:
        return None
    explicit_type = str(element.attrib.get("type") or "").strip()
    if explicit_type:
        return explicit_type.rsplit(".", 1)[-1]
    if message:
        first_line = message.splitlines()[0]
        if ":" in first_line:
            candidate = first_line.split(":", 1)[0].strip().rsplit(".", 1)[-1]
            if candidate:
                return candidate
        match = _ERROR_TYPE_PATTERN.search(message)
        if match:
            return match.group(1)
    tag = _strip_namespace(element.tag)
    return "AssertionError" if tag == "failure" else "Error"


def _assert_location(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    text = "\n".join(
        value
        for value in (
            str(element.attrib.get("message") or ""),
            str(element.text or ""),
        )
        if value.strip()
    )
    match = _LOCATION_PATTERN.search(text.replace("\\", "/"))
    if match is None:
        return None
    return match.group(0).strip().replace("\\", "/")


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _duration_seconds(testcase: ET.Element) -> float:
    try:
        return max(float(testcase.attrib.get("time") or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0
