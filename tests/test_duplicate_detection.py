import hashlib

from engine.duplicates.exact_duplicates import find_exact_duplicates, sha256_file


def test_sha256_file_matches_standard_library(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo bytes")
    assert sha256_file(photo) == hashlib.sha256(b"photo bytes").hexdigest()


def test_detects_only_exact_duplicates(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.png"
    different = tmp_path / "different.jpg"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    different.write_bytes(b"nope")

    groups = find_exact_duplicates([first, second, different, tmp_path / "missing.jpg"])
    assert len(groups) == 1
    assert set(groups[0]) == {first, second}

