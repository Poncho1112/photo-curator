# Performance Audit Report – Photo Curator Milestone 2

## Summary
This report examines the performance-critical paths identified in Milestone‑2‑Scope.md and additional subsystems that could hinder scaling to 10 000 photos. Four concrete hotspots are confirmed; two additional areas were inspected and found to be non‑critical at this scale. For each hotspot we cite the exact source lines, quantify the cost at 10 k photos, and propose a fix tied to the corresponding milestone item.

---

## Hotspot (a): PhotoRepository commits per row  
**Location:** `engine/database/repository.py:60` and `:76`  
**Code:**
```python
    def insert(self, photo: PhotoRecord) -> PhotoRecord:
        ...
        self.connection.commit()
        ...
    def update(self, photo: PhotoRecord) -> PhotoRecord:
        ...
        self.connection.commit()
```
**Cost at 10 k photos:** Each `insert` or `update` calls `connection.commit()`. During an initial index of 10 000 new photos this results in ~10 000 separate transactions; each transaction incurs a filesystem sync, causing significant I/O latency. Subsequent re‑scans that update metadata (e.g., `mark_missing_except`) add further commits.  
**Fix tied to Milestone‑2‑Scope.md:** Batch the index pass into a single transaction. Begin a transaction before processing a batch of photos, call `commit()` once after all inserts/updates, and roll back on any error. This reduces transaction count from O(N) to O(1) per batch, directly addressing the milestone item *“PhotoRepository commits per row … Batch the index pass into one transaction.”*

---

## Hotspot (b): Every scan re‑hashes every file with SHA‑256  
**Location:** `app/workers/scan_worker.py:64` and `engine/duplicates/exact_duplicates.py:11‑16`  
**Code:**
```python
    # scan_worker.py:64
    digest = sha256_file(path)

    # exact_duplicates.py:11‑16
    def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()
```
**Cost at 10 k photos:** Assuming an average JPEG size of 5 MiB, a full scan reads ~50 GiB and computes 10 000 SHA‑256 hashes. If the file set is unchanged, this work is completely wasted and dominates scan time.  
**Fix tied to Milestone‑2‑Scope.md:** Skip hashing when the file’s path, size, and mtime match the stored record. Before calling `sha256_file`, compare `stat().st_size` and `stat().st_mtime` (or the stored `modified_at`) with the current filesystem values; if they match, reuse the existing SHA‑256 from the database. This implements *“Incremental re‑scan: skip SHA‑256 hashing for files whose path, size, and mtime are unchanged.”*

---

## Hotspot (c): `mark_missing_except` resolves every catalog path on every scan  
**Location:** `engine/database/repository.py:105‑110` and `:133‑138`  
**Code:**
```python
    def mark_missing_except(self, existing_paths: set[str], roots: list[str]) -> None:
        for photo in self.list_all():                     # line 106
            belongs = any(_is_beneath(photo.path, root)   # line 107
                          for root in roots)
            if belongs and photo.path not in existing_paths and photo.status != "missing":
                photo.status = "missing"
                self.update(photo)                          # line 110

def _is_beneath(path: str, root: str) -> bool:            # line 133
    try:
        Path(path).resolve().relative_to(Path(root).resolve())  # line 135
        return True
    except ValueError:
        return False
```
**Cost at 10 k photos:** For each of the 10 000 catalog records, and for each root (typically a few), `_is_beneath` calls `Path.resolve()` on the photo path and on the root. `resolve()` performs filesystem stat calls to resolve symlinks and normalize the path. This yields tens of thousands of extra system calls per scan, adding measurable overhead especially on network or slow drives.  
**Fix tied to Milestone‑2‑Scope.md:** Pre‑resolve the roots once before the loop, then resolve each photo path only once and compare against the pre‑resolved root set. This reduces resolve set, making the operation O(photos + roots) instead of O(photos × roots). This satisfies *“mark_missing_except resolves every catalog path on every scan (Path.resolve() per record); make it set‑based.”*

---

## Additional Hotspot: LibraryController._assign_duplicate_groups causes O(N) updates per scan  
**Location:** `app/controllers/library_controller.py:132‑142`  
**Code:**
```python
    def _assign_duplicate_groups(self) -> None:
        records = self.repository.list_all()                     # line 133
        by_hash: dict[str, list[PhotoRecord]] = defaultdict(list)
        for record in records:
            if record.sha256:
                by_hash[record.sha256].append(record)
        for records_with_same_hash in by_hash.values():
            if len(records_with_same_hash) < 2:
                group = None
            else:
                group = records_with_same_hash[0].sha256[:8]
            for record in records_with_same_hash:
                if record.duplicate_group != group:              # line 141
                    record.duplicate_group = group
                    self.repository.update(record)               # line 142
```
**Cost at 10 k photos:** After indexing, this method iterates over all 10 000 records and calls `repository.update` for any record whose `duplicate_group` field would change. In practice, after a fresh scan most records will have their `duplicate_group` set from `None` to a hash prefix (or vice‑versa), triggering an update for nearly every record. Each `update` commits separately (see Hotspot a), adding another ~10 000 transactions.  
**Fix:** Collect all modified records and perform a bulk update inside a single transaction, or add a `bulk_update` method to `PhotoRepository` that executes one `UPDATE` statement per row but commits only once. This aligns with the milestone’s goal of batching database writes.

---

## Additional Hotspot: PhotoGrid.populate creates one widget per record on every refresh  
**Location:** `app/views/photo_grid.py:67‑88`  
**Code:**
```python
    def populate(self, records: list[PhotoRecord], selected_ids: set[int]) -> None:
        previous_ui_selection = self.selected_photo_ids()
        self.clear()
        for record in records:                                 # line 70
            if record.id is None:
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, record.id)  # line 74
            item.setSizeHint(self.gridSize())
            self.addItem(item)                                 # line 76
            tile = PhotoTile(record, record.id in selected_ids, self.thumbnail_width)  # line 77
            ...
            self.setItemWidget(item, tile)                     # line 85
```
**Cost at 10 k photos:** Each call to `populate` (which occurs after every filter change, scan completion, or thumbnail refresh) creates 10 000 `QListWidgetItem` instances and 10 000 `PhotoTile` widgets, even though only a subset is visible at any time. Widget construction and layout are costly, causing noticeable UI lag when filtering or toggling views.  
**Assessment:** This is a genuine scalability risk, though not explicitly listed in the milestone. A standard Qt optimization is to replace `QListWidget` with a `QListView` backed by a custom `QAbstractItemModel` and use a delegate to render tiles only for visible rows (virtualization). This would reduce per‑frame overhead to O(visible items).  

---

## Other Examined Areas (Not Significant at 10k)

- **LibraryController.filtered_records** (`app/controllers/library_controller.py:76‑86`): Performs Python‑level list filtering after a database fetch. At 10 k rows this is negligible (< 1 ms) compared to I/O and widget costs.
- **SearchService** (`engine/search/search_service.py`): Thin wrapper; the underlying `repository.search_paths` uses SQLite `LIKE` queries. Without additional indexes on searched columns, a full‑table scan occurs, but 10 000 rows is trivial for SQLite.
- **FolderPanel.refresh_counts** (`app/views/folder_panel.py:47‑60`): Calls `_aggregate_folder_counts` which loops over records and roots (O(records × roots)). With a few roots, this remains well under a millisecond.
- **Thumbnail request ordering** (`app/ui/main_window.py:335‑374`): Builds thumbnail requests only for files that need them (missing thumbnails) and limits to currently visible records via the grid’s update loop. No unnecessary work.

---

## Risks and Behavior Changes to Avoid

Any optimization must preserve the existing semantics, particularly those related to undo/redo and file‑renaming safety:

1. **Transaction batching** – If a batch insert/update fails part‑way through, the entire batch must be rolled back to maintain atomicity, mirroring the current per‑operation rollback on failure (see `update` lines 73‑75). The public API should still raise the same exceptions so callers can react appropriately.
2. **Skip‑hashing logic** – The decision to reuse an existing SHA‑256 hash must be based **exactly** on equality of the file’s path, size, and modification time (as stored in the record). Any deviation could cause a changed file to retain an old hash, breaking duplicate detection and rename safety.
3. **Path resolution in `mark_missing_except`** – The optimized check must produce the same boolean result as the original `Path(path).resolve().relative_to(Path(root).resolve())` test for every `(path, root)` pair. Using string prefix checks after normalization is acceptable only if it guarantees identical results for all valid inputs.
4. **Duplicate‑group updates** – Deferring updates must not change the timing of when `duplicate_group` becomes visible to other components (e.g., the UI). The final state after the batch must match the state that would have resulted from immediate individual updates.
5. **Widget virtualization** – Switching from `QListWidget` to a model/view approach must preserve selection state, double‑click activation, context menus, and drag‑and‑drop behavior exactly as before. The public signals of `PhotoGrid` should remain unchanged.

By adhering to these constraints, the performance improvements will be safe and backward‑compatible.

