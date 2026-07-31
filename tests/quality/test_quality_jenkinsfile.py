from pathlib import Path


JENKINSFILE = Path(__file__).resolve().parents[2] / "Jenkinsfile"


def test_real_smoke_enables_quality_report_and_email_links_artifact():
    content = JENKINSFILE.read_text(encoding="utf-8")

    assert "$env:QUALITY_ENABLE = '1'" in content
    assert "$env:QUALITY_OUTPUT_DIR = 'reports/quality'" in content
    assert "$env:QUALITY_SHADOW_GATE = '1'" in content
    assert "Map readQualitySummary()" in content
    assert "catch (error)" in content
    assert "artifact/reports/quality/gate-report.md" in content
