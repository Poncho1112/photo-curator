import os

from PIL import Image

from app.workers.scan_worker import ScanJob


def _make_image(path, color):
    Image.new("RGB", (4, 3), color).save(path)


def _scan(folder, known=None):
    return ScanJob([folder], known).run()


def test_unchanged_second_scan_performs_no_sha256_computations(tmp_path, monkeypatch):
    _make_image(tmp_path / "one.jpg", "red")
    _make_image(tmp_path / "two.jpg", "blue")
    first = _scan(tmp_path)
    calls = []

    def counted_hash(path):
        calls.append(path)
        raise AssertionError("unchanged files must not be hashed")

    monkeypatch.setattr("app.workers.scan_worker.sha256_file", counted_hash)
    second = _scan(tmp_path, {record.path: record for record in first})

    assert len(second) == 2
    assert calls == []


def test_changed_file_is_rehashed_and_new_hash_is_recorded(tmp_path, monkeypatch):
    path = tmp_path / "photo.jpg"
    _make_image(path, "red")
    known = _scan(tmp_path)[0]
    _make_image(path, "blue")
    os.utime(path, (known.modified_at + 10, known.modified_at + 10))
    calls = []
    replacement_hash = "f" * 64

    def counted_hash(candidate):
        calls.append(candidate)
        return replacement_hash

    monkeypatch.setattr("app.workers.scan_worker.sha256_file", counted_hash)
    result = _scan(tmp_path, {known.path: known})

    assert calls == [path.resolve()]
    assert result[0].sha256 == replacement_hash


def test_known_record_without_modified_at_is_rehashed(tmp_path, monkeypatch):
    path = tmp_path / "photo.jpg"
    _make_image(path, "red")
    known = _scan(tmp_path)[0]
    known.modified_at = None
    calls = []
    replacement_hash = "e" * 64

    def counted_hash(candidate):
        calls.append(candidate)
        return replacement_hash

    monkeypatch.setattr("app.workers.scan_worker.sha256_file", counted_hash)
    result = _scan(tmp_path, {known.path: known})

    assert calls == [path.resolve()]
    assert result[0].sha256 == replacement_hash


def test_incremental_results_match_full_scan_field_for_field(tmp_path):
    _make_image(tmp_path / "one.jpg", "red")
    _make_image(tmp_path / "two.jpg", "blue")
    full = _scan(tmp_path)

    incremental = _scan(tmp_path, {record.path: record for record in full})

    assert incremental == full
