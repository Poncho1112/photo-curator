"""UI-independent orchestration for the indexed photo library."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.paths import AppPaths
from engine.database.models import PhotoRecord
from engine.database.repository import PhotoRepository
from engine.delete.delete_service import DeleteService, TrashResult
from engine.delete.keep_policy import choose_survivor
from engine.delete.undo_delete_service import UndoDeleteResult, UndoDeleteService
from engine.rename.rename_service import RenameResult, RenameService
from engine.rename.undo_service import UndoResult, UndoService
from engine.search.search_service import SearchService


@dataclass(slots=True)
class FilterState:
    query: str = ""
    duplicates_only: bool = False
    missing_only: bool = False
    renamed_only: bool = False
    selected_only: bool = False


@dataclass(frozen=True, slots=True)
class RenameReviewItem:
    photo_id: int
    current_path: Path
    proposed_path: Path
    sha256: str
    captured_at: str | None
    duplicate_group: str | None
    status: str
    conflict: bool
    missing: bool

    @property
    def safe(self) -> bool:
        return not self.conflict and not self.missing


@dataclass(frozen=True, slots=True)
class DeleteReviewItem:
    group_key: str
    survivor: PhotoRecord
    to_delete: tuple[PhotoRecord, ...]
    reclaimable_bytes: int


class LibraryController:
    def __init__(self, repository: PhotoRepository, paths: AppPaths) -> None:
        self.repository = repository
        self.paths = paths
        self.search_service = SearchService(repository)
        self.filters = FilterState()
        self.rename_selection: set[int] = set()
        self.records: list[PhotoRecord] = []
        self.last_undo_log: Path | None = self._discover_latest_log()
        self.last_delete_log: Path | None = self._discover_latest_delete_log()
        self.load_records()

    def _discover_latest_log(self) -> Path | None:
        logs = sorted(
            (path for path in self.paths.undo_logs.glob("rename-*.jsonl") if path.stat().st_size),
            key=lambda path: path.stat().st_mtime,
        )
        return logs[-1] if logs else None

    def _discover_latest_delete_log(self) -> Path | None:
        logs = sorted(
            (path for path in self.paths.undo_logs.glob("delete-*.jsonl") if path.stat().st_size),
            key=lambda path: path.stat().st_mtime,
        )
        return logs[-1] if logs else None

    def load_records(self) -> list[PhotoRecord]:
        self.records = self.repository.list_all()
        valid_ids = {record.id for record in self.records if record.id is not None}
        self.rename_selection.intersection_update(valid_ids)
        return self.records

    def set_filters(self, **changes: object) -> list[PhotoRecord]:
        for name, value in changes.items():
            if not hasattr(self.filters, name):
                raise ValueError(f"Unknown filter: {name}")
            setattr(self.filters, name, value)
        return self.filtered_records()

    def filtered_records(self) -> list[PhotoRecord]:
        records = self.search_service.search(self.filters.query)
        if self.filters.duplicates_only:
            records = [record for record in records if record.duplicate_group]
        if self.filters.missing_only:
            records = [record for record in records if record.status == "missing" or not Path(record.path).is_file()]
        if self.filters.renamed_only:
            records = [record for record in records if record.status == "renamed" or record.renamed_from]
        if self.filters.selected_only:
            records = [record for record in records if record.id in self.rename_selection]
        return records

    def set_selected_for_rename(self, photo_id: int, selected: bool) -> None:
        if selected:
            self.rename_selection.add(photo_id)
        else:
            self.rename_selection.discard(photo_id)

    def mark_for_rename(self, photo_ids: Iterable[int]) -> None:
        valid = {record.id for record in self.records if record.id is not None}
        self.rename_selection.update(set(photo_ids) & valid)

    def remove_from_rename(self, photo_ids: Iterable[int]) -> None:
        self.rename_selection.difference_update(photo_ids)

    def toggle_rename_selection(self, photo_ids: Iterable[int]) -> None:
        for photo_id in photo_ids:
            self.set_selected_for_rename(photo_id, photo_id not in self.rename_selection)

    def remove_missing_records(self, photo_ids: Iterable[int]) -> int:
        removed = 0
        by_id = {record.id: record for record in self.records}
        for photo_id in set(photo_ids):
            record = by_id.get(photo_id)
            if record and (record.status == "missing" or not Path(record.path).is_file()):
                removed += int(self.repository.delete(photo_id))
                self.rename_selection.discard(photo_id)
        self.load_records()
        return removed

    def index_records(
        self,
        records: Iterable[PhotoRecord],
        roots: Iterable[str | Path],
        *,
        complete_scan: bool = True,
    ) -> list[PhotoRecord]:
        seen: set[str] = set()
        with self.repository.batch():
            for record in records:
                seen.add(record.path)
                self.repository.upsert_by_path(record)
            if complete_scan:
                self.repository.mark_missing_except(seen, [str(root) for root in roots])
            self._assign_duplicate_groups()
        return self.load_records()

    def _assign_duplicate_groups(self) -> None:
        groups: dict[str, list[PhotoRecord]] = {}
        for record in self.repository.list_all():
            if record.status not in {"deleted", "missing"}:
                groups.setdefault(record.sha256, []).append(record)
        for sha256, records in groups.items():
            group = sha256[:8] if len(records) > 1 else None
            for record in records:
                if record.duplicate_group != group:
                    record.duplicate_group = group
                    self.repository.update(record)

    def duplicate_groups(self) -> list[tuple[str, list[PhotoRecord]]]:
        groups: dict[str, list[PhotoRecord]] = {}
        for record in self.records:
            if record.duplicate_group and record.status not in {"deleted", "missing"}:
                groups.setdefault(record.duplicate_group, []).append(record)
        return [
            (group_key, groups[group_key])
            for group_key in sorted(groups)
            if len(groups[group_key]) >= 2
        ]

    def delete_review(
        self,
        policy: str = "shortest_path",
        overrides: dict[str, int] | None = None,
    ) -> list[DeleteReviewItem]:
        overrides = overrides or {}
        review: list[DeleteReviewItem] = []
        for group_key, records in self.duplicate_groups():
            if len({record.sha256 for record in records}) != 1:
                raise ValueError(
                    f"Duplicate group {group_key} contains different hashes; deletion refused"
                )
            if group_key in overrides:
                survivor = next(
                    (record for record in records if record.id == overrides[group_key]),
                    None,
                )
                if survivor is None:
                    raise ValueError(f"Invalid survivor override for duplicate group {group_key}")
            else:
                survivor = choose_survivor(records, policy)
            to_delete = tuple(record for record in records if record is not survivor)
            if not to_delete or survivor in to_delete or len(to_delete) >= len(records):
                raise ValueError(f"Delete review would not retain a survivor for group {group_key}")
            review.append(
                DeleteReviewItem(
                    group_key,
                    survivor,
                    to_delete,
                    sum(record.size for record in to_delete),
                )
            )
        return review

    def export_delete_manifest(
        self,
        target: str | Path,
        review: Iterable[DeleteReviewItem],
    ) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=("group", "action", "path", "size", "sha256"))
            writer.writeheader()
            for item in review:
                writer.writerow(
                    {
                        "group": item.group_key,
                        "action": "keep",
                        "path": item.survivor.path,
                        "size": item.survivor.size,
                        "sha256": item.survivor.sha256,
                    }
                )
                for record in item.to_delete:
                    writer.writerow(
                        {
                            "group": item.group_key,
                            "action": "delete",
                            "path": record.path,
                            "size": record.size,
                            "sha256": record.sha256,
                        }
                    )
        return target

    def delete_duplicates(
        self,
        review: Iterable[DeleteReviewItem],
        service: DeleteService | None = None,
    ) -> list[TrashResult]:
        items = list(review)
        self._validate_delete_review(items)
        targets = [
            (Path(record.path), record.sha256)
            for item in items
            for record in item.to_delete
        ]

        if service is None:
            log = self.paths.undo_logs / f"delete-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
            service = DeleteService(log)
        results = service.delete_paths(targets)
        for result in results:
            if not result.trashed:
                continue
            record = self.repository.get_by_path(result.source)
            if record is not None:
                record.status = "deleted"
                self.repository.update(record)
        if any(result.trashed for result in results):
            self.last_delete_log = service.deletion_log
        self.load_records()
        return results

    def _validate_delete_review(self, items: list[DeleteReviewItem]) -> None:
        """Refuse reviews that no longer retain exactly one live group member."""
        live_groups = {group_key: records for group_key, records in self.duplicate_groups()}
        seen_groups: set[str] = set()
        all_survivor_paths = {Path(item.survivor.path) for item in items}
        all_target_paths = {
            Path(record.path)
            for item in items
            for record in item.to_delete
        }
        if all_survivor_paths.intersection(all_target_paths):
            raise ValueError("Delete targets include a selected survivor")

        for item in items:
            if item.group_key in seen_groups:
                raise ValueError(f"Duplicate delete review group: {item.group_key}")
            seen_groups.add(item.group_key)

            current = live_groups.get(item.group_key)
            if current is None:
                raise ValueError(
                    f"Duplicate group {item.group_key} is no longer eligible for deletion"
                )
            current_by_id = {record.id: record for record in current}
            survivor = current_by_id.get(item.survivor.id)
            if survivor is None or survivor.path != item.survivor.path:
                raise ValueError(
                    f"Selected survivor is not a live member of group {item.group_key}"
                )

            expected_target_ids = set(current_by_id) - {survivor.id}
            actual_target_ids = [record.id for record in item.to_delete]
            if (
                not actual_target_ids
                or len(actual_target_ids) != len(set(actual_target_ids))
                or set(actual_target_ids) != expected_target_ids
            ):
                raise ValueError(
                    f"Delete review must retain exactly one survivor for group {item.group_key}"
                )
            for record in item.to_delete:
                current_record = current_by_id.get(record.id)
                if current_record is None or current_record.path != record.path:
                    raise ValueError(
                        f"Delete target is not a live member of group {item.group_key}"
                    )

    def undo_delete(
        self,
        service: UndoDeleteService | None = None,
    ) -> list[UndoDeleteResult]:
        if service is None:
            if self.last_delete_log is None:
                return []
            service = UndoDeleteService(self.last_delete_log)
        results = service.restore_all()
        for result in results:
            if result.undone:
                record = self.repository.get_by_path(result.restored)
                if record is not None:
                    record.status = "indexed"
                    self.repository.update(record)
        if service.deletion_log.exists() and not service.deletion_log.read_text(encoding="utf-8").strip():
            if self.last_delete_log == service.deletion_log:
                self.last_delete_log = self._discover_latest_delete_log()
        self.load_records()
        return results

    def rename_review(self) -> list[RenameReviewItem]:
        by_id = {record.id: record for record in self.records}
        review: list[RenameReviewItem] = []
        for photo_id in sorted(self.rename_selection):
            record = by_id.get(photo_id)
            if record is None:
                continue
            current = Path(record.path)
            proposed = current.with_name(record.proposed_name or current.name)
            missing = not current.is_file()
            conflict = proposed != current and proposed.exists()
            status = "missing" if missing else "conflict" if conflict else "ready"
            review.append(RenameReviewItem(photo_id, current, proposed, record.sha256, record.captured_at, record.duplicate_group, status, conflict, missing))
        return review

    def export_manifest(self, target: str | Path, review: Iterable[RenameReviewItem] | None = None) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = list(review if review is not None else self.rename_review())
        with target.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=("current_path", "proposed_path", "sha256", "capture_date", "duplicate_group", "rename_status"))
            writer.writeheader()
            for item in rows:
                writer.writerow({"current_path": item.current_path, "proposed_path": item.proposed_path, "sha256": item.sha256, "capture_date": item.captured_at or "", "duplicate_group": item.duplicate_group or "", "rename_status": item.status})
        return target

    def rename_selected(self) -> list[RenameResult]:
        review = self.rename_review()
        if not review or any(not item.safe for item in review):
            raise ValueError("Rename review contains missing files or destination conflicts")
        log = self.paths.undo_logs / f"rename-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl"
        results = RenameService(log).rename_selected(item.current_path for item in review)
        by_source = {str(item.current_path): item for item in review}
        for result in results:
            item = by_source.get(str(result.source))
            if item is None:
                continue
            record = self.repository.get(item.photo_id)
            if record is None:
                continue
            if result.renamed and result.target is not None:
                record.renamed_from = record.path
                record.path = str(result.target)
                record.proposed_name = result.target.name
                record.status = "renamed"
                self.rename_selection.discard(item.photo_id)
            else:
                record.status = f"rename error: {result.error}"
            self.repository.update(record)
        if any(result.renamed for result in results):
            self.last_undo_log = log
        self.load_records()
        return results

    def undo_latest(self) -> list[UndoResult]:
        if self.last_undo_log is None:
            return []
        results = UndoService(self.last_undo_log).restore_all()
        for result in results:
            if result.undone:
                record = self.repository.get_by_path(result.current)
                if record:
                    record.path = str(result.restored)
                    record.proposed_name = result.current.name
                    record.status = "indexed"
                    record.renamed_from = None
                    self.repository.update(record)
        if self.last_undo_log.exists() and not self.last_undo_log.read_text(encoding="utf-8").strip():
            self.last_undo_log = self._discover_latest_log()
        self.load_records()
        return results
