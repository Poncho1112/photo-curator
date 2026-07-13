from engine.database.models import PhotoRecord
from engine.delete.keep_policy import choose_survivor


def record(path, captured_at=None):
    return PhotoRecord(path, "a" * 64, 1, captured_at=captured_at)


def test_shortest_path_keeps_shortest_with_lexicographic_tie_break():
    longer = record("photos/archive/photo.jpg")
    tied_second = record("photos/b.jpg")
    tied_first = record("photos/a.jpg")

    assert choose_survivor([longer, tied_second, tied_first], "shortest_path") is tied_first


def test_oldest_capture_keeps_earliest_capture():
    newer = record("a.jpg", "2026-05-01T12:00:00")
    older = record("much-longer-name.jpg", "2020-01-01T12:00:00")
    unknown = record("x.jpg")

    assert choose_survivor([newer, unknown, older], "oldest_capture") is older


def test_oldest_capture_falls_back_to_shortest_when_all_dates_are_missing():
    longer = record("archive/photo.jpg")
    shorter = record("a.jpg")

    assert choose_survivor([longer, shorter], "oldest_capture") is shorter


def test_preferred_root_overrides_selected_policy():
    shortest = record("C:/a.jpg")
    preferred = record("D:/curated/archive/photo.jpg")

    assert choose_survivor(
        [shortest, preferred],
        "shortest_path",
        preferred_root="D:/curated",
    ) is preferred
