import json
import os
from datetime import date
from pathlib import Path

from engine.rename.rename_service import RenameService


def test_renames_selected_files_only_and_logs_before_rename(tmp_path):
    selected = tmp_path / "one.jpg"
    untouched = tmp_path / "two.jpg"
    selected.write_bytes(b"selected")
    untouched.write_bytes(b"untouched")
    log = tmp_path / "undo.jsonl"

    service = RenameService(log, date_provider=lambda _: date(2026, 7, 10))
    result = service.rename_selected([selected])

    assert len(result) == 1 and result[0].renamed
    assert result[0].target is not None and result[0].target.exists()
    assert not selected.exists()
    assert untouched.read_bytes() == b"untouched"
    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry == {"source": str(selected.resolve()), "target": str(result[0].target.resolve())}


def test_missing_source_is_reported_cleanly(tmp_path):
    missing = tmp_path / "missing.jpg"
    result = RenameService(tmp_path / "undo.jsonl").rename_selected([missing])[0]
    assert not result.renamed
    assert result.error == "source file does not exist"
    assert not (tmp_path / "undo.jsonl").exists()


def test_existing_target_is_never_overwritten(tmp_path):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    service = RenameService(tmp_path / "undo.jsonl", date_provider=lambda _: date(2026, 1, 1))
    # Use the actual temporary folder component, which is intentionally sanitized.
    from engine.duplicates.exact_duplicates import sha256_file
    from engine.rename.naming import generate_name

    expected = source.with_name(generate_name(source.name, source.parent.name, date(2026, 1, 1), sha256_file(source)))
    expected.write_bytes(b"existing")

    result = service.rename_selected([source])[0]
    assert not result.renamed
    assert "overwrite refused" in result.error
    assert source.read_bytes() == b"source"
    assert expected.read_bytes() == b"existing"
    assert not (tmp_path / "undo.jsonl").exists()


def test_rename_os_failure_leaves_source_in_place_with_no_phantom_log(tmp_path, monkeypatch):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    log = tmp_path / "undo.jsonl"
    service = RenameService(log, date_provider=lambda _: date(2026, 1, 1))

    def failing_rename(self, target):
        raise OSError("simulated disk error during rename")

    monkeypatch.setattr(Path, "rename", failing_rename)

    result = service.rename_selected([source])[0]

    assert not result.renamed
    assert "simulated disk error" in result.error
    assert source.read_bytes() == b"source"
    assert not log.exists()


def test_log_failure_rolls_back_rename_and_reports_no_phantom_entry(tmp_path, monkeypatch):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    log = tmp_path / "undo.jsonl"
    service = RenameService(log, date_provider=lambda _: date(2026, 1, 1))

    def failing_write_log(self, source, target):
        raise OSError("simulated log write failure")

    monkeypatch.setattr(RenameService, "_write_log", failing_write_log)

    result = service.rename_selected([source])[0]

    assert not result.renamed
    assert "log" in result.error.lower()
    assert "rolled back" in result.error.lower()
    assert source.exists() and source.read_bytes() == b"source"
    assert result.target is not None and not result.target.exists()
    assert not log.exists()


def test_log_and_rollback_double_failure_reports_actual_filesystem_state(tmp_path, monkeypatch):
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"source")
    log = tmp_path / "undo.jsonl"
    service = RenameService(log, date_provider=lambda _: date(2026, 1, 1))

    from engine.duplicates.exact_duplicates import sha256_file
    from engine.rename.naming import generate_name

    expected_target = source.with_name(
        generate_name(source.name, source.parent.name, date(2026, 1, 1), sha256_file(source))
    )

    def failing_write_log(self, source, target):
        raise OSError("simulated log write failure")

    original_rename = Path.rename

    def rename_that_fails_only_on_rollback(self, target):
        if self == expected_target and Path(target) == source:
            raise OSError("simulated rollback failure")
        return original_rename(self, target)

    monkeypatch.setattr(RenameService, "_write_log", failing_write_log)
    monkeypatch.setattr(Path, "rename", rename_that_fails_only_on_rollback)

    result = service.rename_selected([source])[0]

    assert result.renamed
    assert result.target == expected_target
    assert result.target.exists()
    assert not source.exists()
    assert "undo logging failed" in result.error.lower()
    assert "rollback" in result.error.lower()
    assert not log.exists()


def _stray_temp_files(log: Path) -> list[Path]:
    return list(log.parent.glob(f".{log.name}.*.tmp"))


def test_batch_fsync_failure_preserves_prior_log_bytes_and_cleans_temp(tmp_path, monkeypatch):
    first = tmp_path / "one.jpg"
    second = tmp_path / "two.jpg"
    first.write_bytes(b"first-bytes")
    second.write_bytes(b"second-bytes")
    log = tmp_path / "undo.jsonl"
    service = RenameService(log, date_provider=lambda _: date(2026, 2, 2))

    original_fsync = os.fsync
    call_count = {"n": 0}

    def flaky_fsync(fd):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated fsync failure on second append")
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky_fsync)

    first_result = service.rename_selected([first])[0]
    prior_bytes = log.read_bytes()

    second_result = service.rename_selected([second])[0]

    assert first_result.renamed
    assert not second_result.renamed
    assert "log" in second_result.error.lower()
    assert "rolled back" in second_result.error.lower()

    # Prior log content is byte-for-byte unchanged: no phantom or partial line.
    assert log.read_bytes() == prior_bytes
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry == {"source": str(first.resolve()), "target": str(first_result.target.resolve())}

    # No leftover temp files.
    assert _stray_temp_files(log) == []

    # The second file's rename was rolled back; only the first file's rename stands.
    assert not second_result.target.exists()
    assert second.exists() and second.read_bytes() == b"second-bytes"


def test_batch_write_failure_preserves_prior_log_bytes_and_cleans_temp(tmp_path, monkeypatch):
    first = tmp_path / "one.jpg"
    second = tmp_path / "two.jpg"
    first.write_bytes(b"first-bytes")
    second.write_bytes(b"second-bytes")
    log = tmp_path / "undo.jsonl"
    service = RenameService(log, date_provider=lambda _: date(2026, 3, 3))

    class _FailingWriteFile:
        """Wraps a real file handle but raises on write, without relying on
        instance-attribute shadowing of the underlying C-level file type."""

        def __init__(self, real):
            self._real = real

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self._real.close()
            return False

        def write(self, data):
            raise OSError("simulated write failure on second append")

        def flush(self):
            return self._real.flush()

        def fileno(self):
            return self._real.fileno()

    original_fdopen = os.fdopen
    call_count = {"n": 0}

    def flaky_fdopen(fd, *args, **kwargs):
        call_count["n"] += 1
        handle = original_fdopen(fd, *args, **kwargs)
        if call_count["n"] == 2:
            return _FailingWriteFile(handle)
        return handle

    monkeypatch.setattr(os, "fdopen", flaky_fdopen)

    first_result = service.rename_selected([first])[0]
    prior_bytes = log.read_bytes()

    second_result = service.rename_selected([second])[0]

    assert first_result.renamed
    assert not second_result.renamed
    assert "log" in second_result.error.lower()
    assert "rolled back" in second_result.error.lower()

    assert log.read_bytes() == prior_bytes
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry == {"source": str(first.resolve()), "target": str(first_result.target.resolve())}

    assert _stray_temp_files(log) == []

    assert not second_result.target.exists()
    assert second.exists() and second.read_bytes() == b"second-bytes"


def test_batch_replace_failure_preserves_prior_log_bytes_and_cleans_temp(tmp_path, monkeypatch):
    first = tmp_path / "one.jpg"
    second = tmp_path / "two.jpg"
    first.write_bytes(b"first-bytes")
    second.write_bytes(b"second-bytes")
    log = tmp_path / "undo.jsonl"
    service = RenameService(log, date_provider=lambda _: date(2026, 4, 4))

    original_replace = os.replace
    call_count = {"n": 0}

    def flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated replace failure on second append")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    first_result = service.rename_selected([first])[0]
    prior_bytes = log.read_bytes()

    second_result = service.rename_selected([second])[0]

    assert first_result.renamed
    assert not second_result.renamed
    assert "log" in second_result.error.lower()
    assert "rolled back" in second_result.error.lower()

    assert log.read_bytes() == prior_bytes
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry == {"source": str(first.resolve()), "target": str(first_result.target.resolve())}

    assert _stray_temp_files(log) == []

    assert not second_result.target.exists()
    assert second.exists() and second.read_bytes() == b"second-bytes"


def test_normal_multi_file_batch_retains_all_entries_in_order(tmp_path):
    first = tmp_path / "one.jpg"
    second = tmp_path / "two.jpg"
    third = tmp_path / "three.jpg"
    first.write_bytes(b"first-bytes")
    second.write_bytes(b"second-bytes")
    third.write_bytes(b"third-bytes")
    log = tmp_path / "undo.jsonl"
    service = RenameService(log, date_provider=lambda _: date(2026, 5, 5))

    results = service.rename_selected([first, second, third])

    assert all(result.renamed for result in results)
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    entries = [json.loads(line) for line in lines]
    assert entries == [
        {"source": str(source.resolve()), "target": str(result.target.resolve())}
        for source, result in zip([first, second, third], results)
    ]
    assert _stray_temp_files(log) == []
