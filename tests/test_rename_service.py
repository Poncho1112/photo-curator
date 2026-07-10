import json
from datetime import date

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
