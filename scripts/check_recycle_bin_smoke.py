"""Guarded end-to-end smoke: REAL Windows Recycle Bin + Undo Delete.

Every automated test so far injects a fake trash backend and never touches a
real bin. This exercises the whole Delete Duplicates path for real:

    scan -> index -> delete_review -> delete_duplicates -> undo_delete

against the production IFileOperation backend, on disposable files this script
creates in its own temp directory.

Safety rules enforced here:
  * Operates ONLY inside a freshly created temp root named with a disposable
    prefix; every path it touches is asserted to be under that root.
  * Only ever recycles files this script wrote seconds earlier.
  * Never empties, purges or enumerates the Recycle Bin beyond the exact $R
    paths recorded in this run's own deletion log.
  * On failure it stops and leaves everything in place for inspection rather
    than cleaning up.

Run it from anywhere with the project's Windows interpreter:
    .venv\Scripts\python.exe scripts/check_recycle_bin_smoke.py

Pass --output-dir to keep the evidence JSON somewhere specific; it defaults to
a temp directory. Exit 0 = every check passed.

This is a HOST-ONLY check. It must never run inside a Ringer worker sandbox:
every manifest in this job forbids workers from touching a real Recycle Bin,
and the sandbox cannot launch the Windows interpreter anyway.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DISPOSABLE_PREFIX = "PhotoCurator-RecycleSmoke-"

STEPS: list[dict[str, object]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    STEPS.append({"step": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' :: ' + detail) if detail else ''}")
    return bool(ok)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_jpeg_bytes(colour) -> bytes:
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 48), colour).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def assert_under(path: Path, root: Path) -> None:
    resolved = Path(os.path.normcase(os.path.abspath(path)))
    root_resolved = Path(os.path.normcase(os.path.abspath(root)))
    if os.path.commonpath((str(resolved), str(root_resolved))) != str(root_resolved):
        raise SystemExit(f"REFUSING: {path} is not under the disposable root {root}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    output_dir = Path(
        args.output_dir
        if args.output_dir
        else tempfile.mkdtemp(prefix="PhotoCurator-RecycleSmoke-evidence-")
    ).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        print("This smoke is Windows-only (real Recycle Bin).")
        return 2

    from engine.delete.delete_service import default_trash_backend_name

    backend = default_trash_backend_name()
    if backend != "windows_ifileoperation":
        print(f"REFUSING: default backend is {backend!r}, expected the Windows production backend")
        return 2
    try:
        from engine.delete.windows_recycle_bin import _load_com_modules

        _load_com_modules()
    except OSError as exc:
        print(f"REFUSING: pywin32 COM modules unavailable, cannot prove the real bin: {exc}")
        return 2

    from app.controllers.library_controller import LibraryController
    from app.paths import AppPaths
    from app.workers.scan_worker import ScanJob
    from engine.database.repository import PhotoRepository

    root = Path(tempfile.mkdtemp(prefix=DISPOSABLE_PREFIX)).resolve()
    (root / "THIS-FOLDER-IS-DISPOSABLE.txt").write_text(
        "Created by photo-curator smoke_recycle_bin.py at "
        f"{datetime.now(timezone.utc).isoformat()}\nSafe to delete.\n",
        encoding="utf-8",
    )
    library = root / "library"
    library.mkdir()
    appdata = root / "appdata"

    ok = True
    cleanup = True
    evidence: dict[str, object] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "disposable_root": str(root),
        "backend": backend,
    }

    try:
        duplicate_bytes = make_jpeg_bytes((10, 120, 200))
        unique_bytes = make_jpeg_bytes((200, 30, 40))
        survivor = library / "keep.jpg"
        copies = [library / "nested" / "copy-a.jpg", library / "copy-b.jpg"]
        untouched = library / "unique.jpg"
        (library / "nested").mkdir()
        for path in (survivor, *copies):
            assert_under(path, root)
            path.write_bytes(duplicate_bytes)
        assert_under(untouched, root)
        untouched.write_bytes(unique_bytes)
        duplicate_sha = sha256(survivor)
        evidence["duplicate_sha256"] = duplicate_sha

        paths = AppPaths.from_root(appdata)  # creates the appdata tree first
        repository = PhotoRepository(paths.database)
        records = ScanJob([library]).run()
        ok &= step("scan found all 4 photos", len(records) == 4, f"found {len(records)}")

        controller = LibraryController(repository, paths)
        controller.index_records(records, [library])
        controller.set_roots([str(library)])

        review = controller.delete_review()
        ok &= step("review offers exactly one duplicate group", len(review) == 1, f"groups={len(review)}")
        if not review:
            raise RuntimeError("no duplicate group to delete; aborting before touching the bin")
        item = review[0]
        ok &= step("group keeps one survivor and targets the other two", len(item.to_delete) == 2)
        ok &= step("no skipped groups", not controller.last_delete_review_skips,
                   str(controller.last_delete_review_skips))

        target_paths = [Path(record.path) for record in item.to_delete]
        survivor_path = Path(item.survivor.path)
        for path in (*target_paths, survivor_path):
            assert_under(path, root)

        # ---- the real deletion ----
        results = controller.delete_duplicates(review)
        evidence["results"] = [
            {"source": str(r.source), "trashed": r.trashed, "trashed_to": str(r.trashed_to),
             "undo_logged": r.undo_logged, "error": r.error}
            for r in results
        ]
        ok &= step("both duplicates reported trashed", all(r.trashed for r in results),
                   str([(str(r.source), r.error) for r in results if not r.trashed]))
        ok &= step("both have an undo entry", all(r.undo_logged for r in results))
        ok &= step("target files are gone from disk", all(not p.exists() for p in target_paths))
        ok &= step("survivor is still on disk", survivor_path.is_file())
        ok &= step("survivor bytes unchanged", survivor_path.is_file() and sha256(survivor_path) == duplicate_sha)
        ok &= step("the non-duplicate photo was never touched",
                   untouched.is_file() and sha256(untouched) == hashlib.sha256(unique_bytes).hexdigest())

        log_path = controller.last_delete_log
        ok &= step("deletion log points at this batch", log_path is not None and log_path.is_file(), str(log_path))
        entries = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        evidence["log_entries"] = entries
        ok &= step("log has one entry per deleted file", len(entries) == 2, f"entries={len(entries)}")

        for entry in entries:
            recycled = entry.get("trashed_to")
            ok &= step(f"exact $R path recorded for {Path(str(entry['original_path'])).name}",
                       bool(recycled), str(recycled))
            if recycled:
                recycled_path = Path(str(recycled))
                ok &= step(f"file really is in the Recycle Bin at {recycled_path.name}",
                           recycled_path.is_file(), str(recycled_path))
                if recycled_path.is_file():
                    ok &= step(f"recycled bytes match the catalog hash for {recycled_path.name}",
                               sha256(recycled_path) == entry["sha256"])

        for path in target_paths:
            record = repository.get_by_path(str(path))
            ok &= step(f"catalog marks {path.name} deleted",
                       record is not None and record.status == "deleted",
                       record.status if record else "no row")

        # ---- undo ----
        undo_results = controller.undo_delete()
        evidence["undo"] = [
            {"restored": str(u.restored), "undone": u.undone, "error": u.error} for u in undo_results
        ]
        ok &= step("undo restored both files", all(u.undone for u in undo_results) and len(undo_results) == 2,
                   str([(str(u.restored), u.error) for u in undo_results if not u.undone]))
        ok &= step("both originals are back on disk", all(p.is_file() for p in target_paths))
        ok &= step("restored bytes are byte-identical",
                   all(p.is_file() and sha256(p) == duplicate_sha for p in target_paths))
        for entry in entries:
            recycled = entry.get("trashed_to")
            if recycled:
                ok &= step(f"recycled copy no longer in the bin ({Path(str(recycled)).name})",
                           not Path(str(recycled)).exists())
        for path in target_paths:
            record = repository.get_by_path(str(path))
            ok &= step(f"catalog marks {path.name} indexed again",
                       record is not None and record.status == "indexed",
                       record.status if record else "no row")
        remaining = log_path.read_text(encoding="utf-8").strip() if log_path.is_file() else ""
        ok &= step("deletion log emptied after a full undo", remaining == "", remaining[:200])

        repository.close()
    except Exception:
        ok = False
        cleanup = False
        detail = traceback.format_exc()
        step("smoke completed without raising", False, detail)
        print(detail)

    evidence["status"] = "PASS" if ok else "FAIL"
    evidence["steps"] = STEPS
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    (output_dir / "smoke-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )

    if ok and cleanup:
        shutil.rmtree(root, ignore_errors=True)
        print(f"\ncleaned up disposable root {root}")
    else:
        print(f"\nLEFT IN PLACE for inspection: {root}")
        print("If any file is still in the Recycle Bin, it can be restored from there by hand.")

    passed = sum(1 for s in STEPS if s["ok"])
    print(f"\n{passed}/{len(STEPS)} checks passed -> {evidence['status']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
