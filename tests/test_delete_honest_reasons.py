from pathlib import Path

from app.ui.main_window import format_delete_summary
from engine.delete.delete_service import TrashResult


def _result(name: str, trashed: bool, error: str | None = None, *, undo_logged: bool = True):
    return TrashResult(Path(name), trashed, error=error, undo_logged=undo_logged)


def test_no_skips_mentions_no_skip_cause():
    summary = format_delete_summary([_result("a.jpg", True), _result("b.jpg", True)])

    assert "Moved to Recycle Bin: 2" in summary
    assert "Skipped: 0" in summary
    assert "Skip reasons" not in summary
    assert "changed since indexing" not in summary


def test_distinct_real_skip_reasons_have_correct_counts():
    summary = format_delete_summary(
        [
            _result("a.jpg", False, "source file does not exist"),
            _result("b.jpg", False, "keep copy is missing from disk"),
            _result("c.jpg", False, "source file does not exist"),
            _result("d.jpg", False, "path is outside every added folder"),
        ]
    )

    assert "source file does not exist: 2 file(s)" in summary
    assert "keep copy is missing from disk: 1 file(s)" in summary
    assert "path is outside every added folder: 1 file(s)" in summary


def test_changed_since_indexing_reason_is_reported_when_genuine():
    reason = "file changed since indexing; not deleted"

    assert f"{reason}: 1 file(s)" in format_delete_summary([_result("a.jpg", False, reason)])


def test_raw_oserror_survives_verbatim():
    reason = "[WinError 5] Access is denied"

    assert reason in format_delete_summary([_result("a.jpg", False, reason)])


def test_missing_error_is_counted_under_unknown_reason():
    summary = format_delete_summary([_result("a.jpg", False)])

    assert "Skipped: 1" in summary
    assert "Unknown reason (unrecorded): 1 file(s)" in summary


def test_unlogged_file_is_recycled_and_called_out_separately_from_skips():
    summary = format_delete_summary(
        [
            _result("logged.jpg", True),
            _result("unlogged.jpg", True, "log failed", undo_logged=False),
            _result("skipped.jpg", False, "source file does not exist"),
        ]
    )

    assert "Moved to Recycle Bin: 2" in summary
    assert "Skipped: 1" in summary
    assert "1 file(s) are in the Recycle Bin but have no Undo Delete entry" in summary
