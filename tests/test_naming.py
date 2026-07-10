from datetime import date

import pytest

from engine.rename.naming import generate_name, sanitize_component


def test_generate_name_uses_required_format():
    assert generate_name("Summer Trip.JPG", "Family", date(2026, 7, 4), "abcdef0123456789") == (
        "2026-07-04_Family_Summer-Trip_abcdef01.jpg"
    )


def test_generate_name_sanitizes_unsafe_characters():
    name = generate_name('my<bad>:photo?.png', 'DC / Trip', date(2025, 1, 2), "12345678abcdef00")
    assert name == "2025-01-02_DC-Trip_my-bad-photo_12345678.png"
    assert not any(character in name for character in '<>:"/\\|?*')


@pytest.mark.parametrize(("value", "expected"), [("CON", "_CON"), ("...", "untitled"), ("a   b", "a-b")])
def test_sanitize_component_handles_portability_edge_cases(value, expected):
    assert sanitize_component(value) == expected


def test_generate_name_rejects_invalid_hash():
    with pytest.raises(ValueError):
        generate_name("photo.jpg", "folder", date.today(), "not-a-hash")

