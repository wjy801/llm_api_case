from __future__ import annotations

from module.conftest import HISTORY_LATEST_DIR, _cleanup_old_history_reports


class TestAllureHistoryReport:
    def test_cleanup_old_history_reports_keeps_latest_alias_and_recent_reports(self, tmp_path):
        history_root = tmp_path / "history_report"
        history_root.mkdir()
        for report_name in ("20260720_100000", "20260720_110000", "20260720_120000"):
            (history_root / report_name).mkdir()
        (history_root / HISTORY_LATEST_DIR).mkdir()

        _cleanup_old_history_reports(history_root, keep_limit=2)

        assert (history_root / "20260720_120000").exists()
        assert (history_root / "20260720_110000").exists()
        assert not (history_root / "20260720_100000").exists()
        assert (history_root / HISTORY_LATEST_DIR).exists()

    def test_cleanup_old_history_reports_does_not_delete_when_keep_limit_is_less_than_one(self, tmp_path):
        history_root = tmp_path / "history_report"
        history_root.mkdir()
        (history_root / "20260720_100000").mkdir()

        _cleanup_old_history_reports(history_root, keep_limit=0)

        assert (history_root / "20260720_100000").exists()
