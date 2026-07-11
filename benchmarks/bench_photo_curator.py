"""Milestone 2 performance harness for Photo Curator.

This file is intended to be run with the application's Windows virtualenv.
"""

from __future__ import annotations

import os

# This must be set before importing any application module that may import Qt.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
APP_REPO = Path(r"C:\Users\Poncho\photo-curator")
sys.path.insert(0, str(APP_REPO))

from PIL import Image

from app.controllers.library_controller import LibraryController
from app.paths import AppPaths
from app.workers.scan_worker import ScanJob
from engine.database.repository import PhotoRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10_000, help="number of JPEGs to generate")
    parser.add_argument("--out", type=Path, default=SCRIPT_DIR / "results.json", help="results JSON path")
    parser.add_argument("--keep", action="store_true", help="keep the generated library and database")
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("--count must be greater than zero")
    if not args.out.is_absolute():
        args.out = SCRIPT_DIR / args.out
    return args


def generate_library(library_root: Path, count: int) -> None:
    """Create small JPEGs in 20 nested leaf folders, including exact copies."""
    duplicate_every = 20  # One copied file per 20 files: approximately five percent.
    previous_unique: Path | None = None
    base_mtime = time.time() - (365 * 24 * 60 * 60)

    for index in range(count):
        folder_number = index % 20
        folder = library_root / f"group_{folder_number % 5:02d}" / f"album_{folder_number:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"synthetic_{index:06d}.jpg"

        is_duplicate = index > 0 and index % duplicate_every == duplicate_every - 1
        if is_duplicate and previous_unique is not None:
            shutil.copyfile(previous_unique, target)
        else:
            # The accent pixels make each source distinct while keeping files small.
            color = ((index * 37) % 256, (index * 73) % 256, (index * 109) % 256)
            image = Image.new("RGB", (64, 48), color)
            pixels = image.load()
            for bit in range(24):
                pixels[bit, 0] = (255, 255, 255) if index & (1 << bit) else (0, 0, 0)
            image.save(target, format="JPEG", quality=75, optimize=False)
            previous_unique = target

        varied_mtime = base_mtime + ((index * 97_531) % (365 * 24 * 60 * 60))
        os.utime(target, (varied_mtime, varied_mtime))


def elapsed(callable_obj, /, *args, **kwargs):
    started = time.perf_counter()
    result = callable_obj(*args, **kwargs)
    return time.perf_counter() - started, result


def run_benchmark(count: int, work_root: Path) -> dict[str, object]:
    library_root = work_root / "library"
    app_data_root = work_root / "app-data"

    generate_seconds, _ = elapsed(generate_library, library_root, count)
    scan_seconds, scanned_records = elapsed(ScanJob([library_root]).run)
    if len(scanned_records) != count:
        raise RuntimeError(f"Scan returned {len(scanned_records)} records; expected {count}")

    paths = AppPaths.from_root(app_data_root)
    repository = PhotoRepository(paths.database)
    try:
        controller = LibraryController(repository, paths)
        index_seconds, indexed_records = elapsed(controller.index_records, scanned_records, [library_root])
        if len(indexed_records) != count:
            raise RuntimeError(f"Index contains {len(indexed_records)} records; expected {count}")

        rescan_started = time.perf_counter()
        known = {record.path: record for record in controller.records}
        unchanged_records = ScanJob([library_root], known).run()
        if len(unchanged_records) != count:
            raise RuntimeError(f"Rescan returned {len(unchanged_records)} records; expected {count}")
        controller.index_records(unchanged_records, [library_root])
        rescan_seconds = time.perf_counter() - rescan_started

        search_started = time.perf_counter()
        for query in ("synthetic_000", "album_03", ".jpg", "no-such-photo-token"):
            controller.set_filters(
                query=query,
                duplicates_only=False,
                missing_only=False,
                renamed_only=False,
                selected_only=False,
            )
        search_seconds = time.perf_counter() - search_started

        selected_id = controller.records[0].id
        if selected_id is not None:
            controller.set_selected_for_rename(selected_id, True)
        filter_started = time.perf_counter()
        for active_filter in ("duplicates_only", "missing_only", "renamed_only", "selected_only"):
            state = {
                "query": "",
                "duplicates_only": False,
                "missing_only": False,
                "renamed_only": False,
                "selected_only": False,
            }
            state[active_filter] = True
            controller.set_filters(**state)
        filter_seconds = time.perf_counter() - filter_started
    finally:
        repository.close()

    phases = {
        "generate": float(generate_seconds),
        "scan": float(scan_seconds),
        "index_first": float(index_seconds),
        "rescan_unchanged": float(rescan_seconds),
        "search": float(search_seconds),
        "filters": float(filter_seconds),
    }
    return {
        "count": count,
        "phases": phases,
        "throughput": {
            "scan": count / scan_seconds,
            "index_first": count / index_seconds,
        },
    }


def print_results(results: dict[str, object]) -> None:
    phases = results["phases"]
    throughput = results["throughput"]
    assert isinstance(phases, dict) and isinstance(throughput, dict)
    print(f"Photo Curator Milestone 2 benchmark ({results['count']} photos)")
    print(f"{'Phase':<22} {'Seconds':>12} {'Photos/sec':>14}")
    print("-" * 50)
    for name in ("generate", "scan", "index_first", "rescan_unchanged", "search", "filters"):
        rate = throughput.get(name)
        rate_text = f"{rate:,.2f}" if isinstance(rate, float) else "-"
        print(f"{name:<22} {phases[name]:>12.6f} {rate_text:>14}")


def main() -> None:
    args = parse_args()
    work_root = Path(tempfile.mkdtemp(prefix="photo-curator-m2-", dir=SCRIPT_DIR))
    try:
        results = run_benchmark(args.count, work_root)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print_results(results)
        if args.keep:
            print(f"Kept benchmark data: {work_root}")
    finally:
        if not args.keep:
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    main()
