from pathlib import Path

from engine.delete.delete_service import DeleteGroup, DeleteService
from engine.duplicates.exact_duplicates import sha256_file


class RecordingTrash:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, source: Path) -> None:
        self.calls.append(source)
        return None


def _group(tmp_path, count=1):
    root = tmp_path / "library"
    root.mkdir()
    survivor = root / "keep.jpg"
    survivor.write_bytes(b"same bytes")
    targets = []
    for index in range(count):
        target = root / f"copy-{index}.jpg"
        target.write_bytes(b"same bytes")
        targets.append(target)
    return root, survivor, tuple(targets), sha256_file(survivor)


def _service(tmp_path, recorder, hasher=None):
    return DeleteService(tmp_path / "delete.jsonl", trash_fn=recorder, hasher=hasher)


def test_missing_survivor_refuses_whole_group_without_trash_calls(tmp_path):
    root, survivor, targets, digest = _group(tmp_path)
    survivor.unlink()
    recorder = RecordingTrash()

    results = _service(tmp_path, recorder).delete_groups(
        [DeleteGroup(survivor, digest, targets)], roots=[root]
    )

    assert recorder.calls == []
    assert all(not result.trashed and "missing" in result.error for result in results)


def test_changed_survivor_refuses_whole_group(tmp_path):
    root, survivor, targets, digest = _group(tmp_path)
    survivor.write_bytes(b"changed")
    recorder = RecordingTrash()

    results = _service(tmp_path, recorder).delete_groups(
        [DeleteGroup(survivor, digest, targets)], roots=[root]
    )

    assert recorder.calls == []
    assert all("SHA-256" in result.error for result in results)


def test_target_outside_added_roots_refuses_whole_group(tmp_path):
    root, survivor, targets, digest = _group(tmp_path)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"same bytes")
    recorder = RecordingTrash()

    results = _service(tmp_path, recorder).delete_groups(
        [DeleteGroup(survivor, digest, (*targets, outside))], roots=[root]
    )

    assert recorder.calls == []
    assert len(results) == 2
    assert all("outside" in result.error for result in results)


def test_empty_roots_refuses_whole_group(tmp_path):
    _, survivor, targets, digest = _group(tmp_path)
    recorder = RecordingTrash()

    results = _service(tmp_path, recorder).delete_groups(
        [DeleteGroup(survivor, digest, targets)], roots=[]
    )

    assert recorder.calls == []
    assert all("no folders" in result.error for result in results)


def test_survivor_disappearing_mid_batch_stops_remaining_targets(tmp_path):
    root, survivor, targets, digest = _group(tmp_path, count=2)
    recorder = RecordingTrash()

    def hasher(path: Path) -> str:
        result = sha256_file(path)
        if path == targets[1]:
            survivor.unlink()
        return result

    results = _service(tmp_path, recorder, hasher).delete_groups(
        [DeleteGroup(survivor, digest, targets)], roots=[root]
    )

    assert recorder.calls == [targets[0]]
    assert results[0].trashed
    assert not results[1].trashed and "missing" in results[1].error


def test_valid_in_root_group_trashes_targets_but_never_survivor(tmp_path):
    root, survivor, targets, digest = _group(tmp_path, count=2)
    recorder = RecordingTrash()

    results = _service(tmp_path, recorder).delete_groups(
        [DeleteGroup(survivor, digest, targets)], roots=[root]
    )

    assert all(result.trashed for result in results)
    assert recorder.calls == list(targets)
    assert survivor not in recorder.calls
    assert survivor.read_bytes() == b"same bytes"
