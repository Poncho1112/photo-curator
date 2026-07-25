"""Validate a review/regrade score JSON against rubric-v1.json.

This module is imported directly by ``run_loop.py`` (no subprocess) and is
also runnable as a CLI for manual inspection of a score file. It never calls
a model, never touches git, and never mutates anything -- it only reads the
rubric, reads a score JSON, and reports pass/fail with specific reasons.

Score JSON schema (produced by the read-only review/regrade Claude worker):

    {
      "rubric_version": "v1",
      "rubric_sha256": "<sha256 of rubric-v1.json>",
      "run_name": "photo-curator-100",
      "round": 1,
      "phase": "review" | "regrade",
      "categories": {
        "<category_id>": {"score": <int>, "max": <int>}, ...
      },
      "total": <int>,
      "findings": [
        {"id": "F1", "severity": "P0"|"P1"|"P2"|"P3"|"info",
         "file": "app/ui/main_window.py", "line": 542, "summary": "..."}
      ],
      "host_gates": {"<gate_name>": true|false|null, ...},
      "docs_test_count": {"test_files": <int>, "test_functions": <int>, "docs_files": <int>},
      "runtime_dependencies_declared": true|false,
      "unsupported_platform_messaging_accurate": true|false,
      "cross_platform_destructive_actions": "disabled" | "unsupported_accurate" | "enabled_and_recoverable_claim" | "unspecified",
      "discretionary_override": false,
      "host_evidence_paths": ["ringer-100/state/round-1/windows-verify/evidence.json"]
    }
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

BLOCKING_SEVERITIES = {"P0", "P1", "P2"}
_ALLOWED_SEVERITIES = BLOCKING_SEVERITIES | {"P3", "info"}
_ALLOWED_PLATFORM_STATUSES = {"disabled", "unsupported_accurate"}


@dataclass
class ValidationResult:
    ok: bool
    total: int | None
    failures: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {"ok": self.ok, "total": self.total, "failures": list(self.failures)}


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, *, label: str, failures: list[str]) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(f"could not read {label} at {path}: {exc}")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        failures.append(f"{label} at {path} is not valid JSON: {exc}")
        return None


def _count_current_tests(repo_root: Path) -> dict:
    """Recompute current test file/function counts so a score can't cite stale numbers."""
    test_files = 0
    test_functions = 0
    for tests_dir_name in ("tests", "ringer-100/tests"):
        tests_dir = repo_root / tests_dir_name
        if not tests_dir.is_dir():
            continue
        for path in sorted(tests_dir.rglob("test_*.py")):
            test_files += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    test_functions += 1
    return {"test_files": test_files, "test_functions": test_functions}


def _count_current_docs(repo_root: Path) -> int:
    docs_dir = repo_root / "docs"
    if not docs_dir.is_dir():
        return 0
    return sum(1 for path in docs_dir.rglob("*.md") if path.is_file())


_REQUIRED_UNDO_SCREENSHOTS = {"delete-review.png", "after-delete.png", "after-undo.png"}


def _resolve_evidence_path(raw: str, *, repo_root: Path | None) -> Path:
    candidate = Path(raw)
    base = repo_root if repo_root is not None else Path(".")
    resolved = candidate if candidate.is_absolute() else (base / candidate)
    return resolved.resolve()


def _validate_host_evidence(
    score: dict,
    *,
    repo_root: Path | None,
    state_root: Path | None,
    required_gates: list[str],
    failures: list[str],
) -> None:
    """At a claimed 100, every evidence path must exist, live inside the state root, and
    (for windows_undo_verified) the structured evidence.json plus all three screenshots must
    be present and nonempty. Neither the worker nor the score JSON can manufacture this --
    it is re-derived from the filesystem every time."""
    evidence_paths = score.get("host_evidence_paths")
    if not evidence_paths or not isinstance(evidence_paths, list):
        failures.append("a total of 100 requires host_evidence_paths pointing to host-generated verification")
        return

    state_root_resolved = state_root.resolve() if state_root is not None else None
    resolved_paths: list[Path] = []
    for raw in evidence_paths:
        if not isinstance(raw, str) or not raw:
            failures.append(f"host_evidence_paths entry is not a non-empty string: {raw!r}")
            continue
        resolved = _resolve_evidence_path(raw, repo_root=repo_root)
        if not resolved.is_file():
            failures.append(f"host evidence path does not exist: {raw!r}")
            continue
        if state_root_resolved is not None:
            try:
                resolved.relative_to(state_root_resolved)
            except ValueError:
                failures.append(f"host evidence path is outside the configured state root: {raw!r}")
                continue
        resolved_paths.append(resolved)

    if "windows_undo_verified" not in required_gates:
        return

    json_evidence = [p for p in resolved_paths if p.name == "evidence.json"]
    if not json_evidence:
        failures.append("windows_undo_verified requires a host-generated evidence.json among host_evidence_paths")
    else:
        try:
            undo_evidence = json.loads(json_evidence[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"windows_undo_verified evidence.json could not be read/parsed: {exc}")
        else:
            if not isinstance(undo_evidence, dict) or undo_evidence.get("status") != "passed":
                got = undo_evidence.get("status") if isinstance(undo_evidence, dict) else undo_evidence
                failures.append(f"windows_undo_verified evidence.json does not report status=='passed' (got {got!r})")

    found_screenshots = {p.name: p for p in resolved_paths if p.name in _REQUIRED_UNDO_SCREENSHOTS}
    missing = _REQUIRED_UNDO_SCREENSHOTS - set(found_screenshots)
    if missing:
        failures.append(
            f"windows_undo_verified requires all three screenshots in host_evidence_paths, missing: {sorted(missing)}"
        )
    for name, path in found_screenshots.items():
        if path.stat().st_size == 0:
            failures.append(f"windows_undo_verified screenshot is empty: {name} ({path})")


def validate(
    rubric_path: Path,
    score_path: Path,
    *,
    repo_root: Path | None = None,
    state_root: Path | None = None,
    check_current_counts: bool = True,
) -> ValidationResult:
    failures: list[str] = []

    rubric = _load_json(rubric_path, label="rubric", failures=failures)
    score = _load_json(score_path, label="score", failures=failures)
    if rubric is None or score is None:
        return ValidationResult(ok=False, total=None, failures=failures)

    actual_hash = _sha256_of(rubric_path)
    declared_hash = score.get("rubric_sha256")
    if declared_hash != actual_hash:
        failures.append(
            f"rubric hash mismatch: score declares {declared_hash!r}, "
            f"rubric-v1.json actually hashes to {actual_hash!r}"
        )
    if score.get("rubric_version") != rubric.get("rubric_version"):
        failures.append(
            f"rubric version mismatch: score declares {score.get('rubric_version')!r}, "
            f"rubric-v1.json is {rubric.get('rubric_version')!r}"
        )

    rubric_categories = {c["id"]: c["max"] for c in rubric.get("categories", [])}
    rubric_total_max = rubric.get("total_max")
    score_categories = score.get("categories")
    if not isinstance(score_categories, dict):
        failures.append("score.categories is missing or not an object")
        score_categories = {}

    missing_ids = sorted(set(rubric_categories) - set(score_categories))
    extra_ids = sorted(set(score_categories) - set(rubric_categories))
    if missing_ids:
        failures.append(f"score is missing category ids: {', '.join(missing_ids)}")
    if extra_ids:
        failures.append(f"score has unknown category ids not in rubric: {', '.join(extra_ids)}")

    category_sum = 0
    for cat_id, cat_max in rubric_categories.items():
        entry = score_categories.get(cat_id)
        if not isinstance(entry, dict):
            continue
        declared_max = entry.get("max")
        if declared_max != cat_max:
            failures.append(
                f"category {cat_id!r} max mismatch: score declares {declared_max!r}, rubric says {cat_max!r}"
            )
        cat_score = entry.get("score")
        if not isinstance(cat_score, (int, float)) or cat_score < 0 or cat_score > cat_max:
            failures.append(f"category {cat_id!r} score {cat_score!r} is not within [0, {cat_max}]")
        else:
            category_sum += cat_score

    total = score.get("total")
    if not isinstance(total, (int, float)):
        failures.append(f"score.total is missing or not numeric: {total!r}")
    else:
        if not missing_ids and not extra_ids and total != category_sum:
            hard_caps = rubric.get("hard_caps", [])
            cap_values = {c["cap"] for c in hard_caps}
            if category_sum not in cap_values and total not in cap_values:
                failures.append(
                    f"score.total ({total}) does not equal the sum of category scores ({category_sum}) "
                    f"and does not match any declared hard cap {sorted(cap_values)}"
                )
        if rubric_total_max is not None and total > rubric_total_max:
            failures.append(f"score.total ({total}) exceeds rubric total_max ({rubric_total_max})")

    findings = score.get("findings")
    if not isinstance(findings, list):
        failures.append("score.findings is missing or not a list")
        findings = []
    blocking_findings = []
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            failures.append(f"finding[{i}] is not an object")
            continue
        severity = finding.get("severity")
        file_ref = finding.get("file")
        line_ref = finding.get("line")
        if severity not in _ALLOWED_SEVERITIES:
            failures.append(f"finding[{i}] has invalid severity {severity!r}")
        if not file_ref or not isinstance(file_ref, str):
            failures.append(f"finding[{i}] is missing current file:line evidence (file required)")
        if not isinstance(line_ref, int) or line_ref <= 0:
            failures.append(f"finding[{i}] is missing current file:line evidence (line required)")
        if repo_root is not None and file_ref and isinstance(file_ref, str):
            if not (repo_root / file_ref).is_file():
                failures.append(f"finding[{i}] cites a file that does not exist in the repo: {file_ref!r}")
        if severity in BLOCKING_SEVERITIES:
            blocking_findings.append(finding)

    if score.get("discretionary_override"):
        failures.append("score sets discretionary_override=true; discretionary overrides are always rejected")

    is_hundred = isinstance(total, (int, float)) and total == 100 and rubric_total_max == 100

    host_gates = score.get("host_gates")
    if not isinstance(host_gates, dict):
        failures.append("score.host_gates is missing or not an object")
        host_gates = {}
    required_gates = rubric.get("host_gates", [])
    for gate in required_gates:
        value = host_gates.get(gate)
        if value is False:
            failures.append(f"host gate {gate!r} reports failed (false)")
        if is_hundred and value is not True:
            failures.append(f"a total of 100 requires host gate {gate!r} to be true, got {value!r}")

    if is_hundred:
        if blocking_findings:
            severities = ", ".join(sorted({f.get("severity", "?") for f in blocking_findings}))
            failures.append(f"a total of 100 cannot coexist with P0/P1/P2 findings (found: {severities})")

        _validate_host_evidence(
            score, repo_root=repo_root, state_root=state_root, required_gates=required_gates, failures=failures
        )

        if not score.get("runtime_dependencies_declared"):
            failures.append("a total of 100 requires runtime_dependencies_declared=true")

        if not score.get("unsupported_platform_messaging_accurate"):
            failures.append("a total of 100 requires unsupported_platform_messaging_accurate=true")

        platform_status = score.get("cross_platform_destructive_actions")
        if platform_status not in _ALLOWED_PLATFORM_STATUSES:
            failures.append(
                "a total of 100 requires cross_platform_destructive_actions to be 'disabled' or "
                f"'unsupported_accurate' (got {platform_status!r})"
            )

        docs_test_count = score.get("docs_test_count")
        if not isinstance(docs_test_count, dict):
            failures.append("a total of 100 requires a docs_test_count object")
        elif check_current_counts and repo_root is not None:
            actual = _count_current_tests(repo_root)
            actual["docs_files"] = _count_current_docs(repo_root)
            for key, actual_value in actual.items():
                declared_value = docs_test_count.get(key)
                if declared_value != actual_value:
                    failures.append(
                        f"docs_test_count.{key} is stale: score declares {declared_value!r}, "
                        f"current repo state has {actual_value!r}"
                    )

    ok = not failures
    return ValidationResult(ok=ok, total=total if isinstance(total, (int, float)) else None, failures=failures)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", required=True, type=Path)
    parser.add_argument("--score", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=None, help="repo root, used for file:line and staleness checks")
    parser.add_argument(
        "--state-root",
        type=Path,
        default=None,
        help="state root boundary that all host_evidence_paths must resolve inside, at a claimed 100",
    )
    parser.add_argument(
        "--no-current-counts",
        action="store_true",
        help="skip recomputing current docs/test counts (useful for pre-host review-phase scores)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    result = validate(
        args.rubric,
        args.score,
        repo_root=args.repo,
        state_root=args.state_root,
        check_current_counts=not args.no_current_counts,
    )
    if result.ok:
        print(f"PASS: score validated against rubric v1 (total={result.total})")
    else:
        for failure in result.failures:
            print(f"FAIL: {failure}")
    print("RESULT_JSON: " + json.dumps(result.as_json()))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
