"""Validate a fix-worker's exported patch before it is ever applied to the repo.

Imported directly by ``run_loop.py`` (no subprocess) and also runnable as a
CLI. This module never runs ``git apply`` itself -- it only parses the
unified diff text and the review's ``owned_files`` allowlist, and reports
whether the patch is safe to apply. Applying (``git apply --check`` then
``git apply``) and rollback (``git apply -R``) are the caller's job, using
argument arrays, never shell interpolation of patch/review text.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

# Paths that are never editable by a fix worker, regardless of what the
# review's owned_files allowlist says.
ALWAYS_PROTECTED = {
    "ringer-100/rubric-v1.json",
    "ringer-100/validate_score.py",
    "ringer-100/validate_patch.py",
    "ringer-100/verify_windows_undo.py",
    "ringer-100/manifest.json",
}
ALWAYS_PROTECTED_PREFIXES = (
    ".git/",
    ".ringer-setup/",
)

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>.+) b/(?P<b>.+)$")


@dataclass
class ParsedFileChange:
    path: str
    is_delete: bool = False
    is_rename: bool = False
    rename_from: str | None = None
    rename_to: str | None = None


@dataclass
class PatchValidationResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"ok": self.ok, "failures": list(self.failures), "changed_paths": list(self.changed_paths)}


def _is_traversal_or_absolute(path: str) -> bool:
    if not path:
        return True
    if path.startswith("/") or path.startswith("~"):
        return True
    if PureWindowsPath(path).is_absolute():
        return True
    if re.match(r"^[A-Za-z]:[\\/]", path):
        return True
    parts = re.split(r"[\\/]+", path)
    return any(part == ".." for part in parts)


def parse_unified_diff(patch_text: str) -> list[ParsedFileChange]:
    """Parse ``diff --git`` headers into per-file change records.

    Deliberately minimal: this only needs to recover the changed path and
    whether a hunk is a delete or rename, not reconstruct full patch
    semantics (``git apply --check`` is the source of truth for whether a
    patch actually applies).
    """
    changes: dict[str, ParsedFileChange] = {}
    order: list[str] = []
    lines = patch_text.splitlines()
    current: ParsedFileChange | None = None
    for line in lines:
        match = _DIFF_GIT_RE.match(line)
        if match:
            a_path = match.group("a")
            b_path = match.group("b")
            path = b_path if b_path != "/dev/null" else a_path
            current = ParsedFileChange(path=path)
            if path not in changes:
                order.append(path)
            changes[path] = current
            continue
        if current is None:
            continue
        if line.startswith("deleted file mode"):
            current.is_delete = True
        elif line.startswith("rename from "):
            current.is_rename = True
            current.rename_from = line[len("rename from ") :].strip()
        elif line.startswith("rename to "):
            current.is_rename = True
            current.rename_to = line[len("rename to ") :].strip()
        elif line.startswith("--- "):
            target = line[4:].strip()
            if target == "/dev/null":
                current.is_delete = True
        elif line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current.is_delete = True
    return [changes[path] for path in order]


def validate_patch(
    patch_text: str,
    owned_files: list[str],
    *,
    declared_fix_tests: list[str] | None = None,
) -> PatchValidationResult:
    """Validate ``patch_text`` against the explicit ``owned_files`` allowlist.

    ``declared_fix_tests`` lists test files the review explicitly approved
    the fix worker to touch (e.g. adding a regression test); any other test
    file under ``tests/`` or ``ringer-100/tests/`` is rejected even if it
    happens to be listed in ``owned_files`` by mistake.
    """
    failures: list[str] = []
    declared_fix_tests = declared_fix_tests or []
    owned_set = set(owned_files)

    if not patch_text.strip():
        failures.append("patch is empty")
        return PatchValidationResult(ok=False, failures=failures, changed_paths=[])

    for entry in owned_files:
        if _is_traversal_or_absolute(entry):
            failures.append(f"owned_files entry is an absolute path or path traversal: {entry!r}")

    changes = parse_unified_diff(patch_text)
    if not changes:
        failures.append("no file changes could be parsed from the patch (no 'diff --git' headers found)")

    changed_paths = [change.path for change in changes]

    for change in changes:
        path = change.path
        normalized = path.replace("\\", "/")

        if _is_traversal_or_absolute(path):
            failures.append(f"patch touches an absolute path or path traversal: {path!r}")
            continue

        if change.is_delete:
            failures.append(f"patch deletes a file, which is never allowed for a fix worker: {path!r}")
            continue

        if change.is_rename:
            failures.append(
                f"patch renames a file, which is never allowed for a fix worker: "
                f"{change.rename_from!r} -> {change.rename_to!r}"
            )
            continue

        if normalized in ALWAYS_PROTECTED or path in ALWAYS_PROTECTED:
            failures.append(f"patch touches a protected validator/rubric/manifest file: {path!r}")
            continue

        if any(normalized.startswith(prefix) for prefix in ALWAYS_PROTECTED_PREFIXES):
            failures.append(f"patch touches a protected path (.git or Ringer configuration): {path!r}")
            continue

        is_test_path = normalized.startswith("tests/") or normalized.startswith("ringer-100/tests/")
        if is_test_path and normalized not in set(declared_fix_tests):
            failures.append(
                f"patch touches a test file not declared in this fix's allowlist: {path!r}"
            )
            continue

        if normalized not in owned_set and path not in owned_set:
            failures.append(f"patch touches a file outside the review's owned_files allowlist: {path!r}")

    ok = not failures
    return PatchValidationResult(ok=ok, failures=failures, changed_paths=changed_paths)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument(
        "--allowlist-file",
        required=True,
        type=Path,
        help="JSON file with {'owned_files': [...], 'declared_fix_tests': [...]}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    patch_text = args.patch.read_text(encoding="utf-8")
    allowlist = json.loads(args.allowlist_file.read_text(encoding="utf-8"))
    result = validate_patch(
        patch_text,
        allowlist.get("owned_files", []),
        declared_fix_tests=allowlist.get("declared_fix_tests", []),
    )
    if result.ok:
        print(f"PASS: patch only touches {len(result.changed_paths)} allowlisted, non-deleted, non-renamed file(s)")
    else:
        for failure in result.failures:
            print(f"FAIL: {failure}")
    print("RESULT_JSON: " + json.dumps(result.as_json()))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
