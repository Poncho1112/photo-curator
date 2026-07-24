"""Unit tests for the bounded Ringer-100 loop.

None of these tests invoke a real Ringer run, a real model, or the real
Windows Recycle Bin, and none ever touch the product checkout. Subprocess-
calling functions accept an injectable ``run`` callable everywhere they need
one, and are mostly exercised here with fake runners that record the exact
argument arrays used and return scripted results -- so tests double as
documentation of the safety contract. The one exception is
``TestRoundDeltaTreeSnapshotsRealGitRepo``, which runs the real ``git``
binary against a miniature, throwaway repository created fresh under
``tmp_path`` for each test -- overlapping edits, new-file capture, and "the
real index was never touched" are not things a fake runner can honestly
prove.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

RINGER_100_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RINGER_100_DIR))

import run_loop  # noqa: E402
import validate_patch  # noqa: E402
import validate_score  # noqa: E402
import verify_windows_undo  # noqa: E402

REAL_RUBRIC_PATH = RINGER_100_DIR / "rubric-v1.json"

# A representative WSL-mounted path used wherever a test exercises code that converts a repo
# path into a Windows-native --add-dir argument. Deliberately NOT tied to pytest's tmp_path,
# since tmp_path is host-native (this suite itself runs under Windows Python per the repo's
# verification commands) while run_loop.py's real integration worktree is always /mnt/<drive>/...
# (it only ever runs for real inside WSL; see TestWslHostGate).
FAKE_WSL_WORKTREE = Path("/mnt/c/fake/photo-curator/ringer-100/state/integration-worktree")


@dataclass
class FakeProc:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class SequenceRunner:
    """A fake ``run`` callable that returns scripted results in call order."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        if not self.results:
            raise AssertionError(f"no more fake results queued; got cmd={cmd}")
        return self.results.pop(0)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def rubric_copy(tmp_path):
    """A repo-shaped tmp tree with a byte-identical copy of the real rubric."""
    repo_root = tmp_path / "repo"
    (repo_root / "ringer-100").mkdir(parents=True)
    dest = repo_root / "ringer-100" / "rubric-v1.json"
    shutil.copy(REAL_RUBRIC_PATH, dest)
    return repo_root, dest


def _rubric_dict(rubric_path: Path) -> dict:
    return json.loads(rubric_path.read_text(encoding="utf-8"))


def _full_marks_categories(rubric: dict) -> dict:
    return {c["id"]: {"score": c["max"], "max": c["max"]} for c in rubric["categories"]}


def _base_score(rubric: dict, rubric_path: Path, *, total: int | None = None) -> dict:
    categories = _full_marks_categories(rubric)
    computed_total = sum(entry["score"] for entry in categories.values())
    return {
        "rubric_version": rubric["rubric_version"],
        "rubric_sha256": hashlib.sha256(rubric_path.read_bytes()).hexdigest(),
        "run_name": "photo-curator-100",
        "round": 1,
        "phase": "review",
        "categories": categories,
        "total": total if total is not None else computed_total,
        "findings": [],
        "host_gates": {gate: True for gate in rubric["host_gates"]},
        "docs_test_count": {"test_files": 0, "test_functions": 0, "docs_files": 0},
        "runtime_dependencies_declared": True,
        "unsupported_platform_messaging_accurate": True,
        "cross_platform_destructive_actions": "disabled",
        "discretionary_override": False,
        "host_evidence_paths": ["ringer-100/state/round-01/windows-verify/evidence.json"],
        "owned_files": [],
    }


def _write_undo_evidence_bundle(state_root: Path, round_no: int = 1, *, status: str = "passed") -> list[str]:
    """Writes a fake host-generated evidence.json + 3 screenshots under state_root and returns
    the absolute host_evidence_paths list, mirroring what run_host_verification produces."""
    verify_dir = state_root / f"round-{round_no:02d}" / "windows-verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    (verify_dir / "evidence.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    paths = [str(verify_dir / "evidence.json")]
    for name in ("delete-review.png", "after-delete.png", "after-undo.png"):
        shot = verify_dir / name
        shot.write_bytes(b"\x89PNG\r\n\x1a\nfakepixels")
        paths.append(str(shot))
    return paths


# --------------------------------------------------------------------------
# validate_score.py -- structural gates
# --------------------------------------------------------------------------


class TestValidateScore:
    def test_valid_full_marks_score_passes(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        state_root = repo_root / "ringer-100" / "state"
        score["host_evidence_paths"] = _write_undo_evidence_bundle(state_root)
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=True
        )
        assert result.ok, result.failures
        assert result.total == 100

    def test_rubric_hash_mismatch_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["rubric_sha256"] = "0" * 64
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("rubric hash mismatch" in f for f in result.failures)

    def test_rubric_version_mismatch_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["rubric_version"] = "v2"
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("rubric version mismatch" in f for f in result.failures)

    def test_missing_category_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        del score["categories"]["ux_accessibility"]
        score["total"] = score["total"] - 10
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("missing category ids" in f for f in result.failures)

    def test_unknown_category_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["categories"]["made_up_category"] = {"score": 1, "max": 1}
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("unknown category ids" in f for f in result.failures)

    def test_category_max_mismatch_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["categories"]["functional_correctness"]["max"] = 25
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("max mismatch" in f for f in result.failures)

    def test_total_not_matching_sum_or_hard_cap_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["total"] = 77  # not the category sum (100) and not a declared hard cap
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("does not equal the sum" in f for f in result.failures)

    def test_total_matching_hard_cap_accepted_even_if_not_sum(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["categories"]["ux_accessibility"]["score"] = 0  # sum drops to 90
        score["total"] = 84  # a declared hard cap value
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert result.ok, result.failures

    def test_finding_missing_file_or_line_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["categories"]["ux_accessibility"]["score"] = 5
        score["total"] = 95
        score["findings"] = [{"id": "F1", "severity": "P3", "summary": "no location given"}]
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("missing current file:line evidence" in f for f in result.failures)

    def test_finding_citing_nonexistent_file_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["categories"]["ux_accessibility"]["score"] = 5
        score["total"] = 95
        score["findings"] = [{"id": "F1", "severity": "P3", "file": "app/does_not_exist.py", "line": 1, "summary": "x"}]
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("does not exist in the repo" in f for f in result.failures)

    def test_p0_finding_blocks_a_total_of_100(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        (repo_root / "app").mkdir()
        (repo_root / "app" / "main.py").write_text("x = 1\n")
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["findings"] = [{"id": "F1", "severity": "P0", "file": "app/main.py", "line": 1, "summary": "danger"}]
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("cannot coexist with P0/P1/P2" in f for f in result.failures)

    def test_p3_finding_does_not_block_a_total_of_100(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        (repo_root / "app").mkdir()
        (repo_root / "app" / "main.py").write_text("x = 1\n")
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        state_root = repo_root / "ringer-100" / "state"
        score["host_evidence_paths"] = _write_undo_evidence_bundle(state_root)
        score["findings"] = [{"id": "F1", "severity": "P3", "file": "app/main.py", "line": 1, "summary": "nit"}]
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root, state_root=state_root)
        assert result.ok, result.failures

    def test_failed_host_gate_always_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["categories"]["ux_accessibility"]["score"] = 5
        score["total"] = 95
        score["host_gates"]["native_suite_passed"] = False
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("reports failed" in f for f in result.failures)

    def test_hundred_requires_every_host_gate_true(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["host_gates"]["windows_undo_verified"] = None
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("requires host gate 'windows_undo_verified'" in f for f in result.failures)

    def test_discretionary_override_always_rejected(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["categories"]["ux_accessibility"]["score"] = 5
        score["total"] = 95
        score["discretionary_override"] = True
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("discretionary_override=true" in f for f in result.failures)

    def test_hundred_requires_host_evidence_paths(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["host_evidence_paths"] = []
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("requires host_evidence_paths" in f for f in result.failures)

    def test_hundred_requires_accurate_cross_platform_status(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["cross_platform_destructive_actions"] = "enabled_and_recoverable_claim"
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(rubric_path, score_path, repo_root=repo_root)
        assert not result.ok
        assert any("cross_platform_destructive_actions" in f for f in result.failures)

    def test_hundred_requires_current_docs_test_counts(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        tests_dir = repo_root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_example.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")
        docs_dir = repo_root / "docs"
        docs_dir.mkdir()
        (docs_dir / "readme.md").write_text("# doc\n")
        state_root = repo_root / "ringer-100" / "state"

        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["host_evidence_paths"] = _write_undo_evidence_bundle(state_root)
        score["docs_test_count"] = {"test_files": 999, "test_functions": 999, "docs_files": 999}
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=True
        )
        assert not result.ok
        assert any("is stale" in f for f in result.failures)

        score["docs_test_count"] = {"test_files": 1, "test_functions": 2, "docs_files": 1}
        score_path.write_text(json.dumps(score), encoding="utf-8")
        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=True
        )
        assert result.ok, result.failures

    def test_check_current_counts_false_skips_staleness_check(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        state_root = repo_root / "ringer-100" / "state"
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["host_evidence_paths"] = _write_undo_evidence_bundle(state_root)
        score["docs_test_count"] = {"test_files": 999, "test_functions": 999, "docs_files": 999}
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=False
        )
        assert result.ok, result.failures

    def test_cli_prints_result_json_and_exit_code(self, rubric_copy, tmp_path, capsys):
        repo_root, rubric_path = rubric_copy
        state_root = repo_root / "ringer-100" / "state"
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        score["host_evidence_paths"] = _write_undo_evidence_bundle(state_root)
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        code = validate_score.main(
            ["--rubric", str(rubric_path), "--score", str(score_path), "--repo", str(repo_root),
             "--state-root", str(state_root)]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "RESULT_JSON:" in out
        payload = json.loads(out.splitlines()[-1].split("RESULT_JSON: ", 1)[1])
        assert payload["ok"] is True
        assert payload["total"] == 100

    # --- defect 4: host authority over evidence paths ---------------------

    def test_hundred_rejects_evidence_path_that_does_not_exist(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        state_root = repo_root / "ringer-100" / "state"
        score["host_evidence_paths"] = [str(state_root / "round-01" / "windows-verify" / "evidence.json")]
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=False
        )
        assert not result.ok
        assert any("does not exist" in f for f in result.failures)

    def test_hundred_rejects_evidence_path_outside_state_root(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        state_root = repo_root / "ringer-100" / "state"
        outside_dir = repo_root / "outside-evidence"
        outside_dir.mkdir(parents=True)
        outside_file = outside_dir / "evidence.json"
        outside_file.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
        score["host_evidence_paths"] = [str(outside_file)]
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=False
        )
        assert not result.ok
        assert any("outside the configured state root" in f for f in result.failures)

    def test_windows_undo_verified_requires_evidence_json_status_passed(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        state_root = repo_root / "ringer-100" / "state"
        score["host_evidence_paths"] = _write_undo_evidence_bundle(state_root, status="failed")
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=False
        )
        assert not result.ok
        assert any("status=='passed'" in f for f in result.failures)

    def test_windows_undo_verified_requires_all_three_screenshots(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        state_root = repo_root / "ringer-100" / "state"
        evidence_paths = _write_undo_evidence_bundle(state_root)
        # drop the last screenshot from the declared list
        score["host_evidence_paths"] = evidence_paths[:-1]
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=False
        )
        assert not result.ok
        assert any("missing" in f and "after-undo.png" in f for f in result.failures)

    def test_windows_undo_verified_rejects_empty_screenshot(self, rubric_copy, tmp_path):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        score = _base_score(rubric, rubric_path)
        state_root = repo_root / "ringer-100" / "state"
        evidence_paths = _write_undo_evidence_bundle(state_root)
        # empty out one screenshot file on disk
        Path(evidence_paths[1]).write_bytes(b"")
        score["host_evidence_paths"] = evidence_paths
        score_path = tmp_path / "score.json"
        score_path.write_text(json.dumps(score), encoding="utf-8")

        result = validate_score.validate(
            rubric_path, score_path, repo_root=repo_root, state_root=state_root, check_current_counts=False
        )
        assert not result.ok
        assert any("empty" in f for f in result.failures)


# --------------------------------------------------------------------------
# validate_patch.py -- ownership, deletions, renames, traversal
# --------------------------------------------------------------------------


def _diff_for(path: str, *, deleted: bool = False, rename_from: str | None = None) -> str:
    if rename_from:
        return "\n".join(
            [
                f"diff --git a/{rename_from} b/{path}",
                "similarity index 100%",
                f"rename from {rename_from}",
                f"rename to {path}",
                "",
            ]
        )
    if deleted:
        return "\n".join(
            [
                f"diff --git a/{path} b/{path}",
                "deleted file mode 100644",
                f"--- a/{path}",
                "+++ /dev/null",
                "@@ -1 +0,0 @@",
                "-hello",
                "",
            ]
        )
    return "\n".join(
        [
            f"diff --git a/{path} b/{path}",
            "index 0000000..1111111 100644",
            f"--- a/{path}",
            f"+++ b/{path}",
            "@@ -0,0 +1 @@",
            "+hello",
            "",
        ]
    )


class TestValidatePatch:
    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "C:\\Windows\\system32\\evil.py",
            "engine/../../../etc/passwd",
            "../outside.py",
            "~root/.bashrc",
        ],
    )
    def test_traversal_and_absolute_paths_rejected(self, path):
        assert validate_patch._is_traversal_or_absolute(path)

    def test_ordinary_relative_path_is_not_traversal(self):
        assert not validate_patch._is_traversal_or_absolute("engine/delete/delete_service.py")

    def test_happy_path_owned_file_passes(self):
        patch = _diff_for("engine/delete/delete_service.py")
        result = validate_patch.validate_patch(patch, ["engine/delete/delete_service.py"])
        assert result.ok, result.failures
        assert result.changed_paths == ["engine/delete/delete_service.py"]

    def test_file_outside_allowlist_rejected(self):
        patch = _diff_for("engine/delete/delete_service.py")
        result = validate_patch.validate_patch(patch, ["engine/other_file.py"])
        assert not result.ok
        assert any("outside the review's owned_files allowlist" in f for f in result.failures)

    def test_deletion_rejected_even_if_owned(self):
        patch = _diff_for("engine/delete/delete_service.py", deleted=True)
        result = validate_patch.validate_patch(patch, ["engine/delete/delete_service.py"])
        assert not result.ok
        assert any("deletes a file" in f for f in result.failures)

    def test_rename_rejected_even_if_owned(self):
        patch = _diff_for("engine/delete/new_name.py", rename_from="engine/delete/delete_service.py")
        result = validate_patch.validate_patch(
            patch, ["engine/delete/delete_service.py", "engine/delete/new_name.py"]
        )
        assert not result.ok
        assert any("renames a file" in f for f in result.failures)

    @pytest.mark.parametrize(
        "protected",
        [
            "ringer-100/rubric-v1.json",
            "ringer-100/validate_score.py",
            "ringer-100/validate_patch.py",
            "ringer-100/verify_windows_undo.py",
            "ringer-100/manifest.json",
        ],
    )
    def test_protected_validator_files_rejected_even_if_owned(self, protected):
        patch = _diff_for(protected)
        result = validate_patch.validate_patch(patch, [protected])
        assert not result.ok
        assert any("protected validator/rubric/manifest file" in f for f in result.failures)

    def test_dot_git_path_rejected(self):
        patch = _diff_for(".git/config")
        result = validate_patch.validate_patch(patch, [".git/config"])
        assert not result.ok
        assert any("protected path" in f for f in result.failures)

    def test_ringer_setup_path_rejected(self):
        patch = _diff_for(".ringer-setup/something.json")
        result = validate_patch.validate_patch(patch, [".ringer-setup/something.json"])
        assert not result.ok
        assert any("protected path" in f for f in result.failures)

    def test_undeclared_test_file_rejected_even_if_in_owned_files(self):
        patch = _diff_for("tests/test_delete_service.py")
        result = validate_patch.validate_patch(
            patch, ["tests/test_delete_service.py"], declared_fix_tests=[]
        )
        assert not result.ok
        assert any("not declared in this fix's allowlist" in f for f in result.failures)

    def test_declared_test_file_accepted(self):
        patch = _diff_for("tests/test_delete_service.py")
        result = validate_patch.validate_patch(
            patch, ["tests/test_delete_service.py"], declared_fix_tests=["tests/test_delete_service.py"]
        )
        assert result.ok, result.failures

    def test_empty_patch_rejected(self):
        result = validate_patch.validate_patch("", ["engine/x.py"])
        assert not result.ok

    def test_multi_file_patch_one_bad_file_fails_whole_patch(self):
        patch = _diff_for("engine/ok.py") + "\n" + _diff_for("engine/not_owned.py")
        result = validate_patch.validate_patch(patch, ["engine/ok.py"])
        assert not result.ok
        assert any("not_owned.py" in f for f in result.failures)


# --------------------------------------------------------------------------
# run_loop.py -- environment defaults / WSL detection
# --------------------------------------------------------------------------


class TestEnvironmentDefaults:
    def test_running_under_wsl_true_from_env_var(self, monkeypatch):
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
        assert run_loop.running_under_wsl() is True

    def test_running_under_wsl_false_without_signal(self, monkeypatch):
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.setattr(run_loop.Path, "read_text", lambda self, **k: (_ for _ in ()).throw(OSError()))
        assert run_loop.running_under_wsl() is False

    def test_defaults_resolve_wsl_paths_when_under_wsl(self, monkeypatch):
        monkeypatch.setattr(run_loop, "running_under_wsl", lambda: True)
        assert run_loop.default_ringer_root() == Path("/home/poncho/ringer")
        assert run_loop.default_repo_root() == Path("/mnt/c/Users/Poncho/photo-curator")

    def test_defaults_fall_back_when_not_under_wsl(self, monkeypatch):
        monkeypatch.setattr(run_loop, "running_under_wsl", lambda: False)
        assert run_loop.default_repo_root() == run_loop.REPO_ROOT_FROM_HERE
        assert run_loop.default_ringer_root() == Path.home() / "ringer"


# --------------------------------------------------------------------------
# run_loop.py -- defect 1: WSL host gate
# --------------------------------------------------------------------------


class TestWslHostGate:
    def test_require_wsl_host_passes_under_wsl(self, monkeypatch):
        monkeypatch.setattr(run_loop, "running_under_wsl", lambda: True)
        run_loop.require_wsl_host([])  # should not raise

    def test_require_wsl_host_blocks_windows_native_python(self, monkeypatch):
        monkeypatch.setattr(run_loop, "running_under_wsl", lambda: False)
        with pytest.raises(run_loop.LoopError, match="must run inside WSL"):
            run_loop.require_wsl_host([])

    def test_require_wsl_host_message_names_the_exact_command(self, monkeypatch):
        monkeypatch.setattr(run_loop, "running_under_wsl", lambda: False)
        with pytest.raises(run_loop.LoopError) as excinfo:
            run_loop.require_wsl_host(["--dry-run"])
        message = str(excinfo.value)
        assert "wsl -e bash -lc" in message
        assert "cd /mnt/c/Users/Poncho/photo-curator" in message
        assert "python3 ringer-100/run_loop.py --dry-run" in message

    def test_wsl_invocation_command_includes_all_args(self):
        cmd = run_loop.wsl_invocation_command(["--resume", "--state-root", "/mnt/c/x"])
        assert cmd == (
            "wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && "
            "python3 ringer-100/run_loop.py --resume --state-root /mnt/c/x'"
        )

    def test_wsl_invocation_command_with_no_args(self):
        cmd = run_loop.wsl_invocation_command([])
        assert cmd == "wsl -e bash -lc 'cd /mnt/c/Users/Poncho/photo-curator && python3 ringer-100/run_loop.py'"

    def test_main_blocks_before_any_mutation_when_not_under_wsl(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(run_loop, "running_under_wsl", lambda: False)

        def boom(*a, **k):  # pragma: no cover - must never be reached
            raise AssertionError("main() must fail before touching load_loop_manifest")

        monkeypatch.setattr(run_loop, "load_loop_manifest", boom)
        code = run_loop.main(["--dry-run"])
        assert code == 1
        err = capsys.readouterr().err
        assert "BLOCKED" in err
        assert "wsl -e bash -lc" in err


# --------------------------------------------------------------------------
# run_loop.py -- defect 2: WSL -> Windows-native path conversion for --add-dir
# --------------------------------------------------------------------------


class TestWslToWindowsPathConversion:
    def test_converts_mnt_c_path(self):
        assert run_loop.wsl_mnt_path_to_windows("/mnt/c/Users/Poncho/photo-curator") == "C:\\Users\\Poncho\\photo-curator"

    def test_converts_bare_drive_root(self):
        assert run_loop.wsl_mnt_path_to_windows("/mnt/d") == "D:\\"

    def test_uppercases_drive_letter(self):
        assert run_loop.wsl_mnt_path_to_windows("/mnt/e/foo").startswith("E:\\")

    def test_rejects_non_mnt_path(self):
        with pytest.raises(run_loop.LoopError, match="cannot convert"):
            run_loop.wsl_mnt_path_to_windows("/home/poncho/ringer")

    def test_rejects_windows_native_path(self):
        with pytest.raises(run_loop.LoopError, match="cannot convert"):
            run_loop.wsl_mnt_path_to_windows("C:\\Users\\Poncho\\photo-curator")

    def test_converts_nested_round_dir_shaped_path(self):
        # Mirrors the shape run_host_verification builds for --round-dir/--repo-root/--state-root:
        # a deep path under the state root, several segments below the drive mount.
        assert run_loop.wsl_mnt_path_to_windows(
            "/mnt/c/fake/photo-curator/ringer-100/state/round-03/windows-verify"
        ) == "C:\\fake\\photo-curator\\ringer-100\\state\\round-03\\windows-verify"

    def test_accepts_a_pathlib_path_not_just_a_string(self):
        assert run_loop.wsl_mnt_path_to_windows(Path("/mnt/c/fake/photo-curator")) == "C:\\fake\\photo-curator"

    def test_add_dir_engine_args_shape(self):
        assert run_loop.add_dir_engine_args("C:\\Users\\Poncho\\photo-curator") == [
            "--add-dir=C:\\Users\\Poncho\\photo-curator"
        ]


# --------------------------------------------------------------------------
# run_loop.py -- rubric immutability
# --------------------------------------------------------------------------


class TestRubricImmutability:
    def test_matching_hash_passes(self, rubric_copy):
        repo_root, rubric_path = rubric_copy
        actual_hash = hashlib.sha256(rubric_path.read_bytes()).hexdigest()
        config = {"rubric_path": "ringer-100/rubric-v1.json", "rubric_sha256": actual_hash}
        run_loop.assert_rubric_immutable(config, repo_root)  # should not raise

    def test_mismatched_hash_blocks_the_loop(self, rubric_copy):
        repo_root, rubric_path = rubric_copy
        config = {"rubric_path": "ringer-100/rubric-v1.json", "rubric_sha256": "0" * 64}
        with pytest.raises(run_loop.LoopError, match="rubric hash mismatch"):
            run_loop.assert_rubric_immutable(config, repo_root)

    def test_missing_rubric_file_blocks_the_loop(self, tmp_path):
        config = {"rubric_path": "ringer-100/rubric-v1.json", "rubric_sha256": "0" * 64}
        with pytest.raises(run_loop.LoopError, match="does not exist"):
            run_loop.assert_rubric_immutable(config, tmp_path)

    def test_manifest_json_declares_the_current_rubric_hash(self):
        manifest = json.loads((RINGER_100_DIR / "manifest.json").read_text(encoding="utf-8"))
        actual_hash = hashlib.sha256(REAL_RUBRIC_PATH.read_bytes()).hexdigest()
        assert manifest["rubric_sha256"] == actual_hash


# --------------------------------------------------------------------------
# run_loop.py -- dirty-tree refusal
# --------------------------------------------------------------------------


class TestCleanRepoGuard:
    def test_clean_tree_passes(self):
        runner = SequenceRunner([FakeProc(returncode=0, stdout="")])
        run_loop.assert_clean_repo(Path("/repo"), Path("/repo/ringer-100/state"), run=runner)

    def test_dirty_tree_outside_state_root_blocks(self):
        runner = SequenceRunner([FakeProc(returncode=0, stdout=" M engine/delete/delete_service.py\n")])
        with pytest.raises(run_loop.LoopError, match="not clean"):
            run_loop.assert_clean_repo(Path("/repo"), Path("/repo/ringer-100/state"), run=runner)

    def test_dirty_only_inside_state_root_is_ignored(self, tmp_path):
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        state_root = repo_root / "ringer-100" / "state"
        runner = SequenceRunner([FakeProc(returncode=0, stdout="?? ringer-100/state/round-01/score.json\n")])
        run_loop.assert_clean_repo(repo_root, state_root, run=runner)  # should not raise

    def test_git_status_failure_raises(self):
        runner = SequenceRunner([FakeProc(returncode=128, stdout="", stderr="not a git repository")])
        with pytest.raises(run_loop.LoopError, match="git status failed"):
            run_loop.git_status_porcelain(Path("/repo"), run=runner)


# --------------------------------------------------------------------------
# run_loop.py -- command argument safety
# --------------------------------------------------------------------------


class TestCommandArgumentSafety:
    def test_run_subprocess_never_uses_shell(self, monkeypatch):
        captured = {}

        def fake_subprocess_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return FakeProc(returncode=0)

        monkeypatch.setattr(run_loop.subprocess, "run", fake_subprocess_run)
        run_loop.run_subprocess(["git", "status", "--porcelain"])

        assert isinstance(captured["cmd"], list)
        assert captured["kwargs"].get("shell") is not True

    def test_review_text_is_never_interpolated_into_a_command_string(self):
        malicious_finding = {"file": "x.py; rm -rf /", "line": 1, "summary": "$(whoami) `evil`"}
        # confirmed_deductions text only ever ends up inside a *spec* string handed to a worker,
        # never inside an argv list executed by subprocess -- assert the git/ringer command
        # builders never accept free-form review text as part of a command list.
        cmd = ["git", "-C", "/repo", "status", "--porcelain"]
        assert all(isinstance(part, str) and part not in (malicious_finding["summary"],) for part in cmd)

    def test_ringer_lint_command_is_argument_array(self):
        runner = SequenceRunner([FakeProc(returncode=0)])
        run_loop.ringer_lint(Path("/ringer"), Path("/repo/ringer-100/state/round-01/review-manifest.json"), run=runner)
        cmd, kwargs = runner.calls[0]
        assert cmd[0] == sys.executable
        assert cmd[1] == str(Path("/ringer") / "ringer.py")
        assert cmd[2] == "lint"
        assert cmd[3] == str(Path("/repo/ringer-100/state/round-01/review-manifest.json"))

    def test_ringer_lint_failure_raises(self):
        runner = SequenceRunner([FakeProc(returncode=1, stdout="", stderr="unverifiable check")])
        with pytest.raises(run_loop.LoopError, match="lint failed"):
            run_loop.ringer_lint(Path("/ringer"), Path("/manifest.json"), run=runner)

    def test_ringer_run_command_includes_identity_flag(self):
        runner = SequenceRunner([FakeProc(returncode=0)])
        run_loop.ringer_run(Path("/ringer"), Path("/manifest.json"), run=runner)
        cmd, kwargs = runner.calls[0]
        assert cmd[2] == "run"
        assert "--identity" in cmd
        assert cmd[cmd.index("--identity") + 1] == "codex-orchestrator"


# --------------------------------------------------------------------------
# run_loop.py -- apply / reversibility-check / rollback against the main
# checkout (used only once, by _export_success, on a validated 100)
# --------------------------------------------------------------------------


class TestApplyAndRollback:
    def test_apply_patch_success_calls_check_then_apply(self):
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0)])
        run_loop.apply_patch(Path("/repo"), Path("/patch/fix.patch"), run=runner)
        assert len(runner.calls) == 2
        assert runner.calls[0][0][3:6] == ["apply", "--check", str(Path("/patch/fix.patch"))]
        assert runner.calls[1][0][3:5] == ["apply", str(Path("/patch/fix.patch"))]

    def test_apply_patch_check_failure_raises_before_applying(self):
        runner = SequenceRunner([FakeProc(returncode=1, stderr="context mismatch")])
        with pytest.raises(run_loop.LoopError, match="apply --check failed"):
            run_loop.apply_patch(Path("/repo"), Path("/patch/fix.patch"), run=runner)
        assert len(runner.calls) == 1  # never reached the real apply

    def test_apply_patch_apply_failure_raises(self):
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=1, stderr="boom")])
        with pytest.raises(run_loop.LoopError, match="apply failed"):
            run_loop.apply_patch(Path("/repo"), Path("/patch/fix.patch"), run=runner)

    def test_confirm_patch_reversible_true(self):
        runner = SequenceRunner([FakeProc(returncode=0)])
        assert run_loop.confirm_patch_reversible(Path("/repo"), Path("/p.patch"), run=runner) is True

    def test_confirm_patch_reversible_false(self):
        runner = SequenceRunner([FakeProc(returncode=1)])
        assert run_loop.confirm_patch_reversible(Path("/repo"), Path("/p.patch"), run=runner) is False

    def test_rollback_success_when_snapshot_matches(self):
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0, stdout="")])
        run_loop.rollback_patch(Path("/repo"), Path("/p.patch"), [], run=runner)
        assert runner.calls[0][0][3:5] == ["apply", "-R"]

    def test_rollback_raises_if_apply_r_fails(self):
        runner = SequenceRunner([FakeProc(returncode=1, stderr="cannot reverse")])
        with pytest.raises(run_loop.LoopError, match="rollback failed"):
            run_loop.rollback_patch(Path("/repo"), Path("/p.patch"), [], run=runner)

    def test_rollback_raises_if_snapshot_does_not_match(self):
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0, stdout=" M leftover.py\n")])
        with pytest.raises(run_loop.LoopError, match="did not restore the exact"):
            run_loop.rollback_patch(Path("/repo"), Path("/p.patch"), [], run=runner)


# --------------------------------------------------------------------------
# run_loop.py -- defect 3: integration worktree lifecycle
# --------------------------------------------------------------------------


class TestIntegrationWorktreeCreation:
    def test_create_integration_worktree_resolves_head_then_adds_detached(self, tmp_path):
        repo_root = tmp_path / "repo"
        state_root = repo_root / "ringer-100" / "state"
        runner = SequenceRunner(
            [FakeProc(returncode=0, stdout="deadbeef123\n"), FakeProc(returncode=0)]
        )
        worktree_dir, base_commit = run_loop.create_integration_worktree(repo_root, state_root, run=runner)

        assert worktree_dir == run_loop.integration_worktree_dir(state_root)
        assert base_commit == "deadbeef123"
        assert runner.calls[0][0][3:5] == ["rev-parse", "HEAD"]
        assert runner.calls[1][0][3:6] == ["worktree", "add", "--detach"]
        assert runner.calls[1][0][6] == str(worktree_dir)
        assert runner.calls[1][0][7] == "deadbeef123"

    def test_create_integration_worktree_refuses_if_already_exists(self, tmp_path):
        repo_root = tmp_path / "repo"
        state_root = repo_root / "ringer-100" / "state"
        existing = run_loop.integration_worktree_dir(state_root)
        existing.mkdir(parents=True)
        runner = SequenceRunner([])  # must never be called
        with pytest.raises(run_loop.LoopError, match="already exists"):
            run_loop.create_integration_worktree(repo_root, state_root, run=runner)
        assert not runner.calls

    def test_create_integration_worktree_raises_on_head_failure(self, tmp_path):
        repo_root = tmp_path / "repo"
        state_root = repo_root / "ringer-100" / "state"
        runner = SequenceRunner([FakeProc(returncode=128, stderr="not a git repo")])
        with pytest.raises(run_loop.LoopError, match="could not resolve HEAD"):
            run_loop.create_integration_worktree(repo_root, state_root, run=runner)

    def test_create_integration_worktree_raises_on_worktree_add_failure(self, tmp_path):
        repo_root = tmp_path / "repo"
        state_root = repo_root / "ringer-100" / "state"
        runner = SequenceRunner([FakeProc(returncode=0, stdout="abc\n"), FakeProc(returncode=1, stderr="locked")])
        with pytest.raises(run_loop.LoopError, match="failed to create integration worktree"):
            run_loop.create_integration_worktree(repo_root, state_root, run=runner)


class TestRoundDeltaTreeSnapshotsFakeRunner:
    """Fake-runner tests for the alternate-index tree-snapshot algorithm: call shape, env
    propagation (never shell interpolation), and cleanup of the exact temporary files -- even
    when a git command fails midway."""

    def test_snapshot_calls_read_tree_add_write_tree_with_alternate_index_env(self, tmp_path):
        worktree = tmp_path / "worktree"
        round_scoped_dir = tmp_path / "round-01" / "fix"
        runner = SequenceRunner(
            [FakeProc(returncode=0), FakeProc(returncode=0), FakeProc(returncode=0, stdout="deadbeef1234\n")]
        )
        tree_id = run_loop.snapshot_worktree_tree(worktree, round_scoped_dir, "pre", run=runner)

        assert tree_id == "deadbeef1234"
        assert len(runner.calls) == 3
        assert runner.calls[0][0][3:5] == ["read-tree", "HEAD"]
        assert runner.calls[1][0][3:5] == ["add", "-A"]
        assert runner.calls[2][0][3] == "write-tree"
        expected_alt_index = run_loop.alternate_index_path(round_scoped_dir, "pre")
        for cmd, kwargs in runner.calls:
            assert str(worktree) in cmd
            # env is a genuine mapping passed via subprocess kwargs -- never shell-interpolated
            # into the argv strings themselves.
            assert isinstance(kwargs.get("env"), dict)
            assert kwargs["env"]["GIT_INDEX_FILE"] == str(expected_alt_index)
            assert all("GIT_INDEX_FILE" not in part for part in cmd)

    def test_snapshot_uses_a_distinct_alternate_index_per_tag(self, tmp_path):
        worktree = tmp_path / "worktree"
        round_scoped_dir = tmp_path / "round-01" / "fix"
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0), FakeProc(returncode=0, stdout="t1\n")])
        run_loop.snapshot_worktree_tree(worktree, round_scoped_dir, "pre", run=runner)
        pre_index = runner.calls[0][1]["env"]["GIT_INDEX_FILE"]

        runner2 = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0), FakeProc(returncode=0, stdout="t2\n")])
        run_loop.snapshot_worktree_tree(worktree, round_scoped_dir, "post", run=runner2)
        post_index = runner2.calls[0][1]["env"]["GIT_INDEX_FILE"]

        assert pre_index != post_index

    def test_cleanup_deletes_alt_index_after_success(self, tmp_path):
        worktree = tmp_path / "worktree"
        round_scoped_dir = tmp_path / "round-01" / "fix"
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0), FakeProc(returncode=0, stdout="abc\n")])
        run_loop.snapshot_worktree_tree(worktree, round_scoped_dir, "pre", run=runner)
        assert not run_loop.alternate_index_path(round_scoped_dir, "pre").exists()

    def test_cleanup_runs_even_when_a_git_command_fails(self, tmp_path):
        """A failed git command may leave a real alternate-index (and its .lock) behind; the
        cleanup must still remove exactly those files, not silently leak them."""
        round_scoped_dir = tmp_path / "round-01" / "fix"
        round_scoped_dir.mkdir(parents=True)
        alt_index = run_loop.alternate_index_path(round_scoped_dir, "pre")
        alt_index.write_text("stale alternate index", encoding="utf-8")
        lock_path = alt_index.with_name(alt_index.name + ".lock")
        lock_path.write_text("stale git lock", encoding="utf-8")

        runner = SequenceRunner([FakeProc(returncode=1, stderr="read-tree failed")])
        with pytest.raises(run_loop.LoopError, match="failed to seed"):
            run_loop.snapshot_worktree_tree(tmp_path / "worktree", round_scoped_dir, "pre", run=runner)

        assert not alt_index.exists()
        assert not lock_path.exists()

    def test_cleanup_never_deletes_unrelated_files_in_the_round_directory(self, tmp_path):
        """Cleanup is Path.unlink on the exact alt-index/lock files -- never a directory wipe."""
        round_scoped_dir = tmp_path / "round-01" / "fix"
        round_scoped_dir.mkdir(parents=True)
        sibling = round_scoped_dir / "round-delta.patch"
        sibling.write_text("keep me", encoding="utf-8")

        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0), FakeProc(returncode=0, stdout="abc\n")])
        run_loop.snapshot_worktree_tree(tmp_path / "worktree", round_scoped_dir, "pre", run=runner)

        assert sibling.is_file()
        assert round_scoped_dir.is_dir()

    def test_compute_round_delta_diffs_the_pre_and_post_trees(self, tmp_path):
        worktree = tmp_path / "worktree"
        round_scoped_dir = tmp_path / "round-01" / "fix"
        runner = SequenceRunner(
            [
                FakeProc(returncode=0),  # read-tree HEAD (post snapshot)
                FakeProc(returncode=0),  # add -A (post snapshot)
                FakeProc(returncode=0, stdout="posttree123\n"),  # write-tree (post snapshot)
                FakeProc(returncode=0, stdout="diff --git a/x b/x\n+changed\n"),  # diff --binary pre post
            ]
        )
        delta, post_tree = run_loop.compute_round_delta(worktree, round_scoped_dir, "pretree456", run=runner)

        assert post_tree == "posttree123"
        assert delta == "diff --git a/x b/x\n+changed\n"
        diff_call = runner.calls[3][0]
        assert diff_call[3:5] == ["diff", "--binary"]
        assert diff_call[5] == "pretree456"
        assert diff_call[6] == "posttree123"

    def test_confirm_round_delta_reversible_true_and_false(self, tmp_path):
        assert run_loop.confirm_round_delta_reversible(
            tmp_path, tmp_path / "d.patch", run=SequenceRunner([FakeProc(returncode=0)])
        ) is True
        assert run_loop.confirm_round_delta_reversible(
            tmp_path, tmp_path / "d.patch", run=SequenceRunner([FakeProc(returncode=1)])
        ) is False

    def test_rollback_round_delta_applies_then_reverifies_via_a_fresh_tree_snapshot(self, tmp_path):
        worktree = tmp_path / "worktree"
        round_scoped_dir = tmp_path / "round-01" / "fix"
        runner = SequenceRunner(
            [
                FakeProc(returncode=0),  # apply -R round-delta.patch
                FakeProc(returncode=0), FakeProc(returncode=0), FakeProc(returncode=0, stdout="pretree456\n"),
            ]
        )
        run_loop.rollback_round_delta(
            worktree, round_scoped_dir, tmp_path / "round-delta.patch", "pretree456", run=runner
        )
        assert runner.calls[0][0][3:5] == ["apply", "-R"]
        assert runner.calls[1][0][3:5] == ["read-tree", "HEAD"]

    def test_rollback_round_delta_raises_if_apply_r_fails(self, tmp_path):
        runner = SequenceRunner([FakeProc(returncode=1, stderr="cannot reverse")])
        with pytest.raises(run_loop.LoopError, match="rollback failed"):
            run_loop.rollback_round_delta(
                tmp_path / "worktree", tmp_path / "round-01" / "fix", tmp_path / "round-delta.patch",
                "pretree456", run=runner,
            )

    def test_rollback_round_delta_raises_if_reverified_tree_does_not_match(self, tmp_path):
        runner = SequenceRunner(
            [
                FakeProc(returncode=0),  # apply -R succeeds
                FakeProc(returncode=0), FakeProc(returncode=0), FakeProc(returncode=0, stdout="wrong-tree\n"),
            ]
        )
        with pytest.raises(run_loop.LoopError, match="did not restore the exact pre-round integration worktree tree"):
            run_loop.rollback_round_delta(
                tmp_path / "worktree", tmp_path / "round-01" / "fix", tmp_path / "round-delta.patch",
                "expected-tree", run=runner,
            )


class TestRoundDeltaTreeSnapshotsRealGitRepo:
    """These exercise the real ``git`` binary against a miniature, throwaway repository --
    never the product checkout -- because overlapping edits, new-file capture, and "the real
    index was never touched" are not things a fake runner can honestly prove."""

    @pytest.fixture()
    def real_git_repo(self, tmp_path):
        repo = tmp_path / "miniature-repo"
        repo.mkdir()

        def git(*args):
            proc = run_loop.run_subprocess(["git", "-C", str(repo), *args])
            assert proc.returncode == 0, proc.stderr
            return proc

        git("init", "-q")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Test")
        (repo / "a.py").write_text("line1\nline2\nline3\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", "initial")
        return repo

    def test_overlapping_edits_across_rounds_isolate_correctly(self, real_git_repo, tmp_path):
        repo = real_git_repo

        round1_dir = tmp_path / "round-01" / "fix"
        pre_tree_1 = run_loop.snapshot_worktree_tree(repo, round1_dir, "pre", run=run_loop.run_subprocess)
        (repo / "a.py").write_text("line1\nROUND1-line2\nline3\n", encoding="utf-8")
        delta1, post_tree_1 = run_loop.compute_round_delta(repo, round1_dir, pre_tree_1, run=run_loop.run_subprocess)
        assert "ROUND1-line2" in delta1

        # Round 2 starts exactly where round 1 left off, then edits the SAME line round 1 already
        # touched (the scenario that broke the old reverse-apply-the-pre-round-patch algorithm).
        round2_dir = tmp_path / "round-02" / "fix"
        pre_tree_2 = run_loop.snapshot_worktree_tree(repo, round2_dir, "pre", run=run_loop.run_subprocess)
        assert pre_tree_2 == post_tree_1
        (repo / "a.py").write_text("line1\nROUND2-line2\nline3\n", encoding="utf-8")
        delta2, _post_tree_2 = run_loop.compute_round_delta(repo, round2_dir, pre_tree_2, run=run_loop.run_subprocess)

        # Isolation succeeded at all (no LoopError) despite round 2 touching the very line round 1
        # already changed -- the old reverse-apply-the-pre-round-patch algorithm would have raised
        # "cannot isolate this round's delta" here, since round 1's stored patch no longer
        # reverse-applies once round 2 has changed that same line's content.
        assert "-ROUND1-line2" in delta2  # round 2's own diff base (what round 1 left behind)
        assert "+ROUND2-line2" in delta2  # round 2's own new content
        assert "-line1" not in delta2  # line1 was never touched by either round

    def test_new_untracked_file_is_captured_in_the_round_delta(self, real_git_repo, tmp_path):
        repo = real_git_repo
        round_dir_ = tmp_path / "round-01" / "fix"
        pre_tree = run_loop.snapshot_worktree_tree(repo, round_dir_, "pre", run=run_loop.run_subprocess)
        (repo / "brand_new.py").write_text("brand new content\n", encoding="utf-8")
        delta, _post_tree = run_loop.compute_round_delta(repo, round_dir_, pre_tree, run=run_loop.run_subprocess)

        assert "brand_new.py" in delta
        assert "brand new content" in delta

    def test_real_index_is_never_touched(self, real_git_repo, tmp_path):
        repo = real_git_repo
        round_dir_ = tmp_path / "round-01" / "fix"

        run_loop.snapshot_worktree_tree(repo, round_dir_, "pre", run=run_loop.run_subprocess)
        (repo / "a.py").write_text("line1\nchanged\nline3\n", encoding="utf-8")
        (repo / "untracked.py").write_text("new\n", encoding="utf-8")
        run_loop.snapshot_worktree_tree(repo, round_dir_, "post", run=run_loop.run_subprocess)

        staged = run_loop.run_subprocess(["git", "-C", str(repo), "diff", "--cached", "--name-only"])
        assert staged.stdout.strip() == ""  # nothing was ever staged in the real index

        porcelain = run_loop.run_subprocess(["git", "-C", str(repo), "status", "--porcelain"])
        # the real index still treats the modification as unstaged and the new file as untracked
        assert " M a.py" in porcelain.stdout
        assert "?? untracked.py" in porcelain.stdout

    def test_alternate_index_files_are_cleaned_up_on_disk(self, real_git_repo, tmp_path):
        repo = real_git_repo
        round_dir_ = tmp_path / "round-01" / "fix"
        pre_tree = run_loop.snapshot_worktree_tree(repo, round_dir_, "pre", run=run_loop.run_subprocess)
        assert not run_loop.alternate_index_path(round_dir_, "pre").exists()

        (repo / "a.py").write_text("line1\nchanged\nline3\n", encoding="utf-8")
        run_loop.compute_round_delta(repo, round_dir_, pre_tree, run=run_loop.run_subprocess)
        assert not run_loop.alternate_index_path(round_dir_, "post").exists()
        assert list(round_dir_.glob(".alt-index-*")) == []
        assert list(round_dir_.glob("*.lock")) == []

    def test_alternate_index_cleaned_up_even_when_the_diff_step_fails(self, real_git_repo, tmp_path):
        repo = real_git_repo
        round_dir_ = tmp_path / "round-01" / "fix"
        (repo / "a.py").write_text("line1\nchanged\nline3\n", encoding="utf-8")
        with pytest.raises(run_loop.LoopError, match="git diff --binary"):
            run_loop.compute_round_delta(repo, round_dir_, "0" * 40, run=run_loop.run_subprocess)
        # the "post" snapshot's alternate index must have been cleaned up before the diff's
        # failure ever propagated
        assert list(round_dir_.glob(".alt-index-*")) == []

    def test_rollback_restores_exact_pre_round_tree_including_deleting_new_files(self, real_git_repo, tmp_path):
        repo = real_git_repo
        round_dir_ = tmp_path / "round-01" / "fix"
        pre_tree = run_loop.snapshot_worktree_tree(repo, round_dir_, "pre", run=run_loop.run_subprocess)

        (repo / "a.py").write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
        (repo / "brand_new.py").write_text("new content\n", encoding="utf-8")
        delta, _post_tree = run_loop.compute_round_delta(repo, round_dir_, pre_tree, run=run_loop.run_subprocess)
        delta_path = round_dir_ / "round-delta.patch"
        delta_path.write_text(delta, encoding="utf-8")

        run_loop.rollback_round_delta(repo, round_dir_, delta_path, pre_tree, run=run_loop.run_subprocess)

        assert (repo / "a.py").read_text(encoding="utf-8") == "line1\nline2\nline3\n"
        assert not (repo / "brand_new.py").exists()

    def test_rollback_raises_if_reverified_tree_does_not_match(self, real_git_repo, tmp_path):
        repo = real_git_repo
        round_dir_ = tmp_path / "round-01" / "fix"
        pre_tree = run_loop.snapshot_worktree_tree(repo, round_dir_, "pre", run=run_loop.run_subprocess)

        (repo / "a.py").write_text("line1\nCHANGED\nline3\n", encoding="utf-8")
        delta, _post_tree = run_loop.compute_round_delta(repo, round_dir_, pre_tree, run=run_loop.run_subprocess)
        delta_path = round_dir_ / "round-delta.patch"
        delta_path.write_text(delta, encoding="utf-8")

        with pytest.raises(run_loop.LoopError, match="did not restore the exact pre-round"):
            run_loop.rollback_round_delta(repo, round_dir_, delta_path, "0" * 40, run=run_loop.run_subprocess)


# --------------------------------------------------------------------------
# run_loop.py -- defect 6: host command paths (venv resolution)
# --------------------------------------------------------------------------


class TestResolveWindowsVenvPython:
    def test_resolves_absolute_path_under_main_checkout(self, tmp_path):
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("stub")
        resolved = run_loop.resolve_windows_venv_python(tmp_path)
        assert resolved == venv_python.resolve()
        assert resolved.is_absolute()

    def test_raises_when_venv_missing(self, tmp_path):
        with pytest.raises(run_loop.LoopError, match="Windows venv interpreter not found"):
            run_loop.resolve_windows_venv_python(tmp_path)


# --------------------------------------------------------------------------
# run_loop.py -- host verification gate wiring
# --------------------------------------------------------------------------


class TestHostVerification:
    def _config(self):
        return {
            "host_verification": {
                "commands": [
                    {"name": "native_suite_passed", "cmd": ["{VENV_PYTHON}", "-m", "pytest"]},
                    {
                        "name": "windows_undo_verified",
                        "cmd": ["{VENV_PYTHON}", "{VERIFY_WINDOWS_UNDO_SCRIPT}", "--round-dir"],
                        "round_dir_arg": True,
                        "pass_repo_and_state_root": True,
                    },
                ]
            }
        }

    def _stub_venv(self, main_repo_root: Path) -> Path:
        venv_python = main_repo_root / ".venv" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("stub")
        return venv_python

    def test_all_gates_pass_and_resolve_venv_and_cwd_to_worktree(self, tmp_path):
        main_repo_root = tmp_path / "main-repo"
        venv_python = self._stub_venv(main_repo_root)
        integration_worktree = tmp_path / "worktree"
        state_root = main_repo_root / "ringer-100" / "state"

        config = {"host_verification": {"commands": [{"name": "native_suite_passed", "cmd": ["{VENV_PYTHON}", "-m", "pytest"]}]}}
        runner = SequenceRunner([FakeProc(returncode=0)])
        gates, evidence, evidence_paths = run_loop.run_host_verification(
            config, main_repo_root, integration_worktree, 1, state_root, run=runner
        )
        assert gates == {"native_suite_passed": True}
        cmd, kwargs = runner.calls[0]
        assert cmd[0] == str(venv_python)
        assert kwargs["cwd"] == str(integration_worktree)
        assert evidence_paths == []

    def test_one_gate_fails(self, tmp_path):
        main_repo_root = tmp_path / "main-repo"
        self._stub_venv(main_repo_root)
        state_root = main_repo_root / "ringer-100" / "state"
        config = {"host_verification": {"commands": [{"name": "compileall_passed", "cmd": ["{VENV_PYTHON}", "-m", "compileall"]}]}}
        runner = SequenceRunner([FakeProc(returncode=1, stderr="SyntaxError")])
        gates, evidence, evidence_paths = run_loop.run_host_verification(
            config, main_repo_root, tmp_path / "worktree", 1, state_root, run=runner
        )
        assert gates == {"compileall_passed": False}
        assert any("SyntaxError" in line for line in evidence)
        assert evidence_paths == []

    def test_targeted_tests_skipped_when_review_names_none(self, tmp_path):
        main_repo_root = tmp_path / "main-repo"
        self._stub_venv(main_repo_root)
        state_root = main_repo_root / "ringer-100" / "state"
        config = {
            "host_verification": {
                "commands": [{"name": "targeted_tests_passed", "cmd": ["{VENV_PYTHON}", "-m", "pytest"], "targets_from_review": True}]
            }
        }
        runner = SequenceRunner([])  # must not be called at all
        gates, evidence, _paths = run_loop.run_host_verification(
            config, main_repo_root, tmp_path / "worktree", 1, state_root, review_score={}, run=runner
        )
        assert gates == {"targeted_tests_passed": True}
        assert not runner.calls

    def test_targeted_tests_appended_from_review(self, tmp_path):
        main_repo_root = tmp_path / "main-repo"
        self._stub_venv(main_repo_root)
        state_root = main_repo_root / "ringer-100" / "state"
        config = {
            "host_verification": {
                "commands": [{"name": "targeted_tests_passed", "cmd": ["{VENV_PYTHON}", "-m", "pytest"], "targets_from_review": True}]
            }
        }
        runner = SequenceRunner([FakeProc(returncode=0)])
        gates, evidence, _paths = run_loop.run_host_verification(
            config, main_repo_root, tmp_path / "worktree", 1, state_root,
            review_score={"targeted_tests": ["tests/test_x.py"]}, run=runner,
        )
        assert gates == {"targeted_tests_passed": True}
        assert runner.calls[0][0][-1] == "tests/test_x.py"

    def test_missing_venv_raises_before_any_command_runs(self, tmp_path):
        main_repo_root = tmp_path / "main-repo-without-venv"
        state_root = main_repo_root / "ringer-100" / "state"
        runner = SequenceRunner([])
        with pytest.raises(run_loop.LoopError, match="venv interpreter not found"):
            run_loop.run_host_verification(self._config(), main_repo_root, tmp_path / "worktree", 1, state_root, run=runner)
        assert not runner.calls


# --------------------------------------------------------------------------
# run_loop.py -- defect: host-gate commands executed by the Windows-native venv
# Python must receive Windows-native arguments, not raw WSL /mnt/<drive>/...
# paths. WSL interop translates only the executable's own path, never its
# arguments, so an unconverted argument like "/mnt/c/.../verify_windows_undo.py"
# is read by Windows as rooted at the current drive ("C:\mnt\c\...") -- exit 2,
# every round forced to roll back. Deliberately uses realistic /mnt/c paths,
# not tmp_path: tmp_path is host-native (this suite runs under Windows Python
# per the repo's verification commands -- see FAKE_WSL_WORKTREE above), while
# run_host_verification's real inputs are always /mnt/<drive>/... in
# production (the controller only ever runs under WSL). resolve_windows_venv_python
# is monkeypatched instead of stubbed on disk: a literal WindowsPath("/mnt/c/...")
# has no drive, so writing "under" it on this test machine would land for real
# at C:\mnt\c\... -- the exact corrupted location this defect produces.
# --------------------------------------------------------------------------


class TestHostVerificationWindowsNativeConversion:
    _FAKE_MAIN_REPO_ROOT = Path("/mnt/c/fake/photo-curator")
    _FAKE_VENV_PYTHON = _FAKE_MAIN_REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    _FAKE_STATE_ROOT = _FAKE_MAIN_REPO_ROOT / "ringer-100" / "state"
    _FAKE_INTEGRATION_WORKTREE = _FAKE_STATE_ROOT / "integration-worktree"

    def _patch_fake_venv(self, monkeypatch):
        monkeypatch.setattr(run_loop, "resolve_windows_venv_python", lambda main_repo_root: self._FAKE_VENV_PYTHON)

    def _config(self):
        return {
            "host_verification": {
                "commands": [
                    {"name": "native_suite_passed", "cmd": ["{VENV_PYTHON}", "-m", "pytest"]},
                    {
                        "name": "windows_undo_verified",
                        "cmd": ["{VENV_PYTHON}", "{VERIFY_WINDOWS_UNDO_SCRIPT}", "--round-dir"],
                        "round_dir_arg": True,
                        "pass_repo_and_state_root": True,
                    },
                ]
            }
        }

    def test_converts_script_round_dir_repo_root_and_state_root_to_windows_native(self, monkeypatch):
        self._patch_fake_venv(monkeypatch)
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0)])
        gates, evidence, evidence_paths = run_loop.run_host_verification(
            self._config(), self._FAKE_MAIN_REPO_ROOT, self._FAKE_INTEGRATION_WORKTREE, 3, self._FAKE_STATE_ROOT,
            run=runner,
        )
        assert gates == {"native_suite_passed": True, "windows_undo_verified": True}

        windows_undo_cmd, _kwargs = runner.calls[1]
        assert windows_undo_cmd[0] == str(self._FAKE_VENV_PYTHON)  # the interpreter's own path: WSL interop
        # handles this automatically, so it is deliberately left in WSL form, unconverted.

        # This is the regression assertion: the observed defect handed the raw WSL string
        # ("/mnt/c/fake/photo-curator/ringer-100/verify_windows_undo.py", etc.) straight through.
        # A converted, native "C:\..." value proves the fix; the raw form proves its absence.
        assert windows_undo_cmd[1] == "C:\\fake\\photo-curator\\ringer-100\\verify_windows_undo.py"
        assert "/mnt/c" not in windows_undo_cmd[1]

        assert windows_undo_cmd[2] == "--round-dir"
        assert windows_undo_cmd[3] == "C:\\fake\\photo-curator\\ringer-100\\state\\round-03\\windows-verify"

        assert windows_undo_cmd[4] == "--repo-root"
        assert windows_undo_cmd[5] == "C:\\fake\\photo-curator\\ringer-100\\state\\integration-worktree"
        assert windows_undo_cmd[6] == "--state-root"
        assert windows_undo_cmd[7] == "C:\\fake\\photo-curator\\ringer-100\\state"
        assert len(windows_undo_cmd) == 8

        # Controller-side evidence stays canonical WSL: the WSL controller (never the Windows
        # process) reads these paths back off disk after the round.
        expected_verify_dir = self._FAKE_STATE_ROOT / "round-03" / "windows-verify"
        assert evidence_paths == [
            str(expected_verify_dir / "evidence.json"),
            str(expected_verify_dir / "delete-review.png"),
            str(expected_verify_dir / "after-delete.png"),
            str(expected_verify_dir / "after-undo.png"),
        ]

    def test_relative_pytest_targets_and_flags_are_not_mangled(self, monkeypatch):
        self._patch_fake_venv(monkeypatch)
        config = {
            "host_verification": {
                "commands": [
                    {"name": "compileall_passed", "cmd": ["{VENV_PYTHON}", "-m", "compileall", "-q", "app", "engine"]},
                    {
                        "name": "targeted_tests_passed",
                        "cmd": ["{VENV_PYTHON}", "-m", "pytest", "-q"],
                        "targets_from_review": True,
                    },
                ]
            }
        }
        runner = SequenceRunner([FakeProc(returncode=0), FakeProc(returncode=0)])
        gates, _evidence, _paths = run_loop.run_host_verification(
            config, self._FAKE_MAIN_REPO_ROOT, self._FAKE_INTEGRATION_WORKTREE, 1, self._FAKE_STATE_ROOT,
            review_score={"targeted_tests": ["tests/test_x.py::test_y"]}, run=runner,
        )
        assert gates == {"compileall_passed": True, "targeted_tests_passed": True}

        compileall_cmd, _ = runner.calls[0]
        assert compileall_cmd[1:] == ["-m", "compileall", "-q", "app", "engine"]

        targeted_cmd, _ = runner.calls[1]
        assert targeted_cmd[1:] == ["-m", "pytest", "-q", "tests/test_x.py::test_y"]

    def test_git_based_host_gates_keep_working_unconverted(self, monkeypatch):
        self._patch_fake_venv(monkeypatch)
        config = {"host_verification": {"commands": [{"name": "diff_check_clean", "cmd": ["git", "diff", "--check"]}]}}
        runner = SequenceRunner([FakeProc(returncode=0)])
        gates, _evidence, _paths = run_loop.run_host_verification(
            config, self._FAKE_MAIN_REPO_ROOT, self._FAKE_INTEGRATION_WORKTREE, 1, self._FAKE_STATE_ROOT, run=runner,
        )
        assert gates == {"diff_check_clean": True}
        cmd, kwargs = runner.calls[0]
        assert cmd == ["git", "diff", "--check"]
        assert kwargs["cwd"] == str(self._FAKE_INTEGRATION_WORKTREE)

    def test_fails_closed_when_a_windows_bound_path_is_not_representable(self, monkeypatch):
        self._patch_fake_venv(monkeypatch)
        config = {
            "host_verification": {
                "commands": [
                    {
                        "name": "windows_undo_verified",
                        "cmd": ["{VENV_PYTHON}", "{VERIFY_WINDOWS_UNDO_SCRIPT}", "--round-dir"],
                        "round_dir_arg": True,
                        "pass_repo_and_state_root": True,
                    },
                ]
            }
        }
        unrepresentable_state_root = Path("/home/poncho/not-under-mnt")  # not a WSL '/mnt/<drive>/...' path
        runner = SequenceRunner([FakeProc(returncode=0)])  # must never be reached
        with pytest.raises(run_loop.LoopError, match="cannot convert"):
            run_loop.run_host_verification(
                config, self._FAKE_MAIN_REPO_ROOT, self._FAKE_INTEGRATION_WORKTREE, 1,
                unrepresentable_state_root, run=runner,
            )
        assert not runner.calls  # fails closed before this gate's command ever runs


# --------------------------------------------------------------------------
# run_loop.py -- atomic state writes
# --------------------------------------------------------------------------


class TestAtomicState:
    def test_atomic_write_json_writes_and_cleans_up_tmp(self, tmp_path):
        target = tmp_path / "nested" / "state.json"
        run_loop.atomic_write_json(target, {"a": 1})
        assert target.is_file()
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
        assert not target.with_suffix(target.suffix + ".tmp").exists()

    def test_atomic_write_text_writes_and_cleans_up_tmp(self, tmp_path):
        target = tmp_path / "nested" / "diff.patch"
        run_loop.atomic_write_text(target, "diff --git a/x b/x\n")
        assert target.read_text(encoding="utf-8") == "diff --git a/x b/x\n"
        assert not target.with_suffix(target.suffix + ".tmp").exists()

    def test_save_and_load_state_round_trip(self, tmp_path):
        state = {"run_name": "photo-curator-100", "rounds": [], "score_history": [], "status": "in_progress", "next_round": 1}
        run_loop.save_state(tmp_path, state)
        loaded = run_loop.load_state(tmp_path)
        assert loaded == state

    def test_load_state_returns_none_when_absent(self, tmp_path):
        assert run_loop.load_state(tmp_path / "nope") is None


# --------------------------------------------------------------------------
# run_loop.py -- plateau / max-round stop conditions
# --------------------------------------------------------------------------


class TestPlateau:
    @pytest.mark.parametrize(
        "history,expected",
        [
            ([], False),
            ([70], False),
            ([70, 80], False),
            ([70, 80, 90], False),
            ([70, 80, 80], False),  # only one non-improving transition
            ([70, 75, 74, 74], True),  # last two transitions both non-improving
            ([70, 90, 100], False),
        ],
    )
    def test_is_plateaued_default_two_rounds(self, history, expected):
        assert run_loop.is_plateaued(history, plateau_rounds=2) is expected

    def test_is_plateaued_needs_more_history_for_higher_plateau_rounds(self):
        assert run_loop.is_plateaued([80, 80, 80], plateau_rounds=3) is False
        assert run_loop.is_plateaued([80, 80, 80, 80], plateau_rounds=3) is True


# --------------------------------------------------------------------------
# run_loop.py -- manifest materialization (add-dir, score-worker.json, no
# nested Ringer worktree for the fix phase, identical review/regrade prompt)
# --------------------------------------------------------------------------


class TestManifestMaterialization:
    def _config(self):
        return {
            "run_name": "photo-curator-100",
            "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800,
            "rubric_path": "ringer-100/rubric-v1.json",
        }

    def test_review_and_regrade_use_the_identical_rubric_protocol_text(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        config = self._config()
        state_root = tmp_path / "state"
        review = run_loop.build_review_manifest(config, rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE)
        regrade = run_loop.build_regrade_manifest(config, rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE)
        assert review["run_name"] == regrade["run_name"] == config["run_name"]
        assert rubric["review_protocol"] in review["tasks"][0]["spec"]
        assert rubric["review_protocol"] in regrade["tasks"][0]["spec"]

    def test_review_manifest_grants_add_dir_and_writes_to_own_artifact_path(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        config = self._config()
        state_root = tmp_path / "state"
        review = run_loop.build_review_manifest(config, rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE)
        task = review["tasks"][0]
        assert task["engine_args"] == ["--add-dir=C:\\fake\\photo-curator\\ringer-100\\state\\integration-worktree"]
        assert task["expect_files"] == ["score-worker.json"]
        assert "./score-worker.json" in task["spec"]
        assert FAKE_WSL_WORKTREE.as_posix() in task["spec"]

    def test_regrade_manifest_embeds_host_authoritative_gates_hint(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        config = self._config()
        state_root = tmp_path / "state"
        regrade = run_loop.build_regrade_manifest(
            config, rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE,
            host_gates_hint={"native_suite_passed": True, "windows_undo_verified": False},
        )
        spec = regrade["tasks"][0]["spec"]
        assert "host-authoritative" in spec
        assert '"windows_undo_verified": false' in spec
        assert regrade["tasks"][0]["expect_files"] == ["score-worker.json"]

    def test_fix_manifest_edits_worktree_directly_without_spawning_a_nested_ringer_worktree(self, tmp_path):
        config = self._config()
        state_root = tmp_path / "state"
        manifest, allowlist_path = run_loop.build_fix_manifest(
            config, 1, state_root, tmp_path, FAKE_WSL_WORKTREE,
            ["engine/delete/delete_service.py"],
            ["engine/delete/delete_service.py"],
            [],
            [],
            [{"severity": "P1", "file": "engine/delete/delete_service.py", "line": 10, "summary": "bug"}],
        )
        assert "worktrees" not in manifest
        spec = manifest["tasks"][0]["spec"]
        assert "MUST NEVER run any git command" in spec
        assert "ringer-100/rubric-v1.json" in spec  # named as forbidden, not given as content
        assert "engine/delete/delete_service.py" in spec
        assert FAKE_WSL_WORKTREE.as_posix() in spec
        assert manifest["tasks"][0]["engine_args"] == [
            "--add-dir=C:\\fake\\photo-curator\\ringer-100\\state\\integration-worktree"
        ]
        assert str(allowlist_path) == str(state_root / "round-01" / "fix" / "allowlist.json")

    def test_fix_manifest_check_diffs_the_worktree_not_a_nested_ringer_worktree(self, tmp_path):
        config = self._config()
        state_root = tmp_path / "state"
        manifest, _allowlist_path = run_loop.build_fix_manifest(
            config, 1, state_root, tmp_path, FAKE_WSL_WORKTREE, ["x.py"], ["x.py"], [], [], []
        )
        check = manifest["tasks"][0]["check"]
        assert f"git -C {FAKE_WSL_WORKTREE.as_posix()} diff --binary" in check


# --------------------------------------------------------------------------
# run_loop.py -- review/regrade artifact location: Ringer creates a task's
# real cwd at <manifest workdir>/<task key>, one level below the phase's own
# workdir. The worker must write (and expect_files must declare) exactly
# "score-worker.json" relative to that cwd -- never an absolute path back up
# at the phase directory (the task cwd's unauthorized parent).
# --------------------------------------------------------------------------


class TestReviewRegradeArtifactLocation:
    def _config(self):
        return {
            "run_name": "photo-curator-100",
            "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800,
            "rubric_path": "ringer-100/rubric-v1.json",
        }

    def test_review_task_expect_files_is_exactly_the_relative_filename(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        state_root = tmp_path / "state"
        review = run_loop.build_review_manifest(self._config(), rubric, 3, state_root, tmp_path, FAKE_WSL_WORKTREE)
        task = review["tasks"][0]
        assert task["expect_files"] == ["score-worker.json"]
        assert task["key"] == "review-round-03"

    def test_regrade_task_expect_files_is_exactly_the_relative_filename(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        state_root = tmp_path / "state"
        regrade = run_loop.build_regrade_manifest(self._config(), rubric, 3, state_root, tmp_path, FAKE_WSL_WORKTREE)
        task = regrade["tasks"][0]
        assert task["expect_files"] == ["score-worker.json"]
        assert task["key"] == "regrade-round-03"

    def test_review_check_references_the_relative_filename_not_an_absolute_phase_path(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        state_root = tmp_path / "state"
        review = run_loop.build_review_manifest(self._config(), rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE)
        check = review["tasks"][0]["check"]
        phase_dir = run_loop.review_phase_dir(state_root, 1)
        assert "--score score-worker.json" in check
        assert "--score " + (phase_dir / "score-worker.json").as_posix() not in check
        assert phase_dir.as_posix() not in check  # the check never names the (unauthorized) phase dir
        # the rubric input is still an absolute WSL path -- only score-worker.json is relative
        assert f"--rubric {(tmp_path / 'ringer-100' / 'rubric-v1.json').as_posix()}" in check

    def test_regrade_check_references_the_relative_filename_not_an_absolute_phase_path(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        state_root = tmp_path / "state"
        regrade = run_loop.build_regrade_manifest(self._config(), rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE)
        check = regrade["tasks"][0]["check"]
        phase_dir = run_loop.regrade_phase_dir(state_root, 1)
        assert "--score score-worker.json" in check
        assert "--score " + (phase_dir / "score-worker.json").as_posix() not in check
        assert phase_dir.as_posix() not in check

    def test_review_task_grants_add_dir_only_to_the_worktree_never_phase_or_main_repo(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        state_root = tmp_path / "state"
        review = run_loop.build_review_manifest(self._config(), rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE)
        engine_args = review["tasks"][0]["engine_args"]
        assert len(engine_args) == 1
        assert engine_args[0] == run_loop.add_dir_engine_args(
            run_loop.wsl_mnt_path_to_windows(FAKE_WSL_WORKTREE)
        )[0]
        # never an --add-dir naming the phase dir or the main repo root
        assert str(state_root) not in engine_args[0]
        assert str(tmp_path) not in engine_args[0]

    def test_review_worker_score_path_is_under_the_task_key_not_the_phase_dir(self):
        state_root = Path("/mnt/c/fake/photo-curator/ringer-100/state")
        path = run_loop.review_worker_score_path(state_root, 5)
        phase_dir = run_loop.review_phase_dir(state_root, 5)
        assert path == phase_dir / "review-round-05" / "score-worker.json"
        assert path.parent != phase_dir  # never <phase_dir>/score-worker.json (the task cwd's parent)

    def test_regrade_worker_score_path_is_under_the_task_key_not_the_phase_dir(self):
        state_root = Path("/mnt/c/fake/photo-curator/ringer-100/state")
        path = run_loop.regrade_worker_score_path(state_root, 5)
        phase_dir = run_loop.regrade_phase_dir(state_root, 5)
        assert path == phase_dir / "regrade-round-05" / "score-worker.json"
        assert path.parent != phase_dir

    def test_controller_reads_review_score_from_the_exact_ringer_task_layout(self, tmp_path):
        """Ringer's real task layout is <manifest workdir>/<task key>: for the review manifest,
        workdir is the phase dir and the task key is review_task_key(round_no). The controller
        must read from exactly that path, not the phase dir itself."""
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        config = self._config()
        state_root = tmp_path / "state"
        review = run_loop.build_review_manifest(config, rubric, 2, state_root, tmp_path, FAKE_WSL_WORKTREE)
        ringer_task_cwd = Path(review["workdir"]) / review["tasks"][0]["key"]
        assert run_loop.review_worker_score_path(state_root, 2) == ringer_task_cwd / "score-worker.json"

    def test_controller_reads_regrade_score_from_the_exact_ringer_task_layout(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        config = self._config()
        state_root = tmp_path / "state"
        regrade = run_loop.build_regrade_manifest(config, rubric, 2, state_root, tmp_path, FAKE_WSL_WORKTREE)
        ringer_task_cwd = Path(regrade["workdir"]) / regrade["tasks"][0]["key"]
        assert run_loop.regrade_worker_score_path(state_root, 2) == ringer_task_cwd / "score-worker.json"


# --------------------------------------------------------------------------
# run_loop.py -- defect 5: manifest feasibility / structural lint
# --------------------------------------------------------------------------


class TestManifestShapeLint:
    def _valid_manifest(self):
        return {
            "run_name": "x",
            "workdir": "/mnt/c/somewhere",
            "tasks": [
                {
                    "key": "t1",
                    "task_type": "code-review",
                    "engine": "claude",
                    "model": "sonnet",
                    "spec": "A" * 30,
                    "check": "python3 /x/validate.py --score /x/score.json",
                    "expect_files": ["/x/score.json"],
                    "timeout_s": 60,
                    "verified": "score exists and validates",
                }
            ],
        }

    def test_valid_manifest_has_no_problems(self):
        assert run_loop.validate_manifest_shape(self._valid_manifest()) == []

    def test_missing_run_name_flagged(self):
        manifest = self._valid_manifest()
        del manifest["run_name"]
        assert any("run_name" in p for p in run_loop.validate_manifest_shape(manifest))

    def test_empty_tasks_flagged(self):
        manifest = self._valid_manifest()
        manifest["tasks"] = []
        assert any("no tasks" in p for p in run_loop.validate_manifest_shape(manifest))

    def test_unverifiable_check_flagged(self):
        manifest = self._valid_manifest()
        manifest["tasks"][0]["check"] = "true"
        assert any("unverifiable check" in p for p in run_loop.validate_manifest_shape(manifest))

    def test_missing_expect_files_flagged(self):
        manifest = self._valid_manifest()
        manifest["tasks"][0]["expect_files"] = []
        assert any("no expect_files" in p for p in run_loop.validate_manifest_shape(manifest))

    def test_missing_required_task_field_flagged(self):
        manifest = self._valid_manifest()
        del manifest["tasks"][0]["timeout_s"]
        assert any("timeout_s" in p for p in run_loop.validate_manifest_shape(manifest))

    def test_materialize_round_manifests_for_lint_produces_no_problems(self, tmp_path):
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        config = {
            "run_name": "photo-curator-100",
            "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800,
            "rubric_path": "ringer-100/rubric-v1.json",
        }
        state_root = tmp_path / "state"
        problems = run_loop.materialize_round_manifests_for_lint(
            config, rubric, 1, state_root, tmp_path, FAKE_WSL_WORKTREE
        )
        assert problems == {"review": [], "fix": [], "regrade": []}


# --------------------------------------------------------------------------
# run_loop.py -- dry-run (no mutation, no model calls)
# --------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_prints_plan_without_touching_disk_or_subprocess(self, capsys):
        config = {
            "run_name": "photo-curator-100",
            "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800,
            "rubric_path": "ringer-100/rubric-v1.json",
            "max_rounds": 10,
            "plateau_rounds": 2,
        }
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        repo_root = Path("/mnt/c/fake/photo-curator")
        state_root = repo_root / "ringer-100" / "state"
        runner = SequenceRunner([])  # must never be called

        orchestrator = run_loop.LoopOrchestrator(
            config=config, rubric=rubric, repo_root=repo_root, ringer_root=Path("/mnt/c/fake/ringer"),
            state_root=state_root, run=runner,
        )
        orchestrator.dry_run()

        assert not runner.calls
        assert not (state_root / "state.json").exists()
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["next_round"] == 1
        assert "review_manifest_preview" in payload
        assert payload["integration_worktree_preview"] == str(run_loop.integration_worktree_dir(state_root))


# --------------------------------------------------------------------------
# run_loop.py -- resume semantics, success, plateau-stop, max-round-stop
# --------------------------------------------------------------------------


class TestOrchestratorRunLoop:
    def _orchestrator(self, tmp_path, *, max_rounds=10, plateau_rounds=2):
        config = {
            "run_name": "photo-curator-100",
            "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800,
            "rubric_path": "ringer-100/rubric-v1.json",
            "max_rounds": max_rounds,
            "plateau_rounds": plateau_rounds,
        }
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        state_root = tmp_path / "state"
        orchestrator = run_loop.LoopOrchestrator(
            config=config, rubric=rubric, repo_root=tmp_path, ringer_root=Path("/ringer"),
            state_root=state_root, run=SequenceRunner([]),
        )
        # These tests exercise plateau/success/resume bookkeeping with _run_round overridden;
        # stub worktree creation so it doesn't need real subprocess calls queued.
        orchestrator._ensure_integration_worktree = lambda state: (
            state.setdefault("integration_worktree", str(FAKE_WSL_WORKTREE)),
            setattr(orchestrator, "integration_worktree", FAKE_WSL_WORKTREE),
        )
        return orchestrator

    def test_success_on_first_hundred(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path)
        orchestrator._run_round = lambda round_no, state: {
            "round": round_no, "score_total": 100, "status": "completed",
            "commands_evidence": [], "last_confirmed_deductions": [], "regrade_ok": True, "regrade_failures": [],
        }
        orchestrator._export_success = lambda state: None
        code = orchestrator.run_loop(resume=False)
        assert code == 0
        state = run_loop.load_state(orchestrator.state_root)
        assert state["status"] == "success"
        assert not (orchestrator.state_root / "BLOCKED.md").exists()

    def test_plateau_stops_before_max_rounds(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path, max_rounds=10, plateau_rounds=2)
        orchestrator._run_round = lambda round_no, state: {
            "round": round_no, "score_total": 80, "status": "completed",
            "commands_evidence": [], "last_confirmed_deductions": [], "regrade_ok": True, "regrade_failures": [],
        }
        code = orchestrator.run_loop(resume=False)
        assert code == 1
        state = run_loop.load_state(orchestrator.state_root)
        assert state["status"] == "blocked"
        assert state["next_round"] == 4  # stopped after round 3 (3 equal scores -> 2 non-improving transitions)
        assert (orchestrator.state_root / "BLOCKED.md").is_file()
        assert "plateaued" in (orchestrator.state_root / "BLOCKED.md").read_text(encoding="utf-8")

    def test_max_rounds_stops_when_still_improving(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path, max_rounds=3, plateau_rounds=2)
        scores = {"n": 50}

        def fake_round(round_no, state):
            scores["n"] += 10
            return {
                "round": round_no, "score_total": scores["n"], "status": "completed",
                "commands_evidence": [], "last_confirmed_deductions": [], "regrade_ok": True, "regrade_failures": [],
            }

        orchestrator._run_round = fake_round
        code = orchestrator.run_loop(resume=False)
        assert code == 1
        state = run_loop.load_state(orchestrator.state_root)
        assert state["status"] == "blocked"
        assert len(state["rounds"]) == 3
        assert "max_rounds=3" in (orchestrator.state_root / "BLOCKED.md").read_text(encoding="utf-8")

    def test_resume_without_prior_state_starts_fresh(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path)
        calls = []
        orchestrator._run_round = lambda round_no, state: calls.append(round_no) or {
            "round": round_no, "score_total": 100, "status": "completed",
            "commands_evidence": [], "last_confirmed_deductions": [], "regrade_ok": True, "regrade_failures": [],
        }
        orchestrator._export_success = lambda state: None
        orchestrator.run_loop(resume=True)
        assert calls == [1]

    def test_resume_continues_from_persisted_next_round(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path)
        run_loop.save_state(
            orchestrator.state_root,
            {"run_name": "photo-curator-100", "rounds": [{"round": 1, "score_total": 70}], "score_history": [70],
             "status": "in_progress", "next_round": 2, "integration_worktree": str(FAKE_WSL_WORKTREE)},
        )
        calls = []
        orchestrator._run_round = lambda round_no, state: calls.append(round_no) or {
            "round": round_no, "score_total": 100, "status": "completed",
            "commands_evidence": [], "last_confirmed_deductions": [], "regrade_ok": True, "regrade_failures": [],
        }
        orchestrator._export_success = lambda state: None
        code = orchestrator.run_loop(resume=True)
        assert code == 0
        assert calls == [2]

    def test_resume_on_already_successful_run_is_a_noop(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path)
        run_loop.save_state(
            orchestrator.state_root,
            {"run_name": "photo-curator-100", "rounds": [], "score_history": [100], "status": "success", "next_round": 2},
        )
        orchestrator._run_round = lambda round_no, state: pytest.fail("must not run another round")
        code = orchestrator.run_loop(resume=True)
        assert code == 0

    def test_resume_on_already_blocked_run_is_a_noop(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path)
        run_loop.save_state(
            orchestrator.state_root,
            {"run_name": "photo-curator-100", "rounds": [], "score_history": [70], "status": "blocked", "next_round": 2},
        )
        orchestrator._run_round = lambda round_no, state: pytest.fail("must not run another round")
        code = orchestrator.run_loop(resume=True)
        assert code == 1

    def test_starting_fresh_with_existing_state_and_no_resume_flag_raises(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path)
        run_loop.save_state(
            orchestrator.state_root,
            {"run_name": "photo-curator-100", "rounds": [], "score_history": [70], "status": "in_progress", "next_round": 2},
        )
        with pytest.raises(run_loop.LoopError, match="pass --resume"):
            orchestrator.run_loop(resume=False)

    def test_loop_error_during_a_round_writes_blocked_md(self, tmp_path):
        orchestrator = self._orchestrator(tmp_path)

        def boom(round_no, state):
            raise run_loop.LoopError("simulated integration-safety failure")

        orchestrator._run_round = boom
        code = orchestrator.run_loop(resume=False)
        assert code == 1
        state = run_loop.load_state(orchestrator.state_root)
        assert state["status"] == "blocked"
        assert "simulated integration-safety failure" in (orchestrator.state_root / "BLOCKED.md").read_text(encoding="utf-8")

    # --- defect 3: main checkout is never touched while blocked -------------

    def test_main_checkout_never_touched_while_looping_to_a_block(self, tmp_path):
        """The only function ever allowed to run a git command against repo_root is
        _export_success, and that only runs on a validated 100. Across an entire plateau-to-
        block run (with _run_round faked), self.run must see nothing but the two
        worktree-creation calls -- never anything addressed at repo_root."""
        real_repo_root = tmp_path / "main-checkout"
        real_repo_root.mkdir()
        state_root = real_repo_root / "ringer-100" / "state"
        config = {
            "run_name": "photo-curator-100", "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800, "rubric_path": "ringer-100/rubric-v1.json",
            "max_rounds": 10, "plateau_rounds": 2,
        }
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        runner = SequenceRunner([FakeProc(returncode=0, stdout="deadbeef\n"), FakeProc(returncode=0)])
        orchestrator = run_loop.LoopOrchestrator(
            config=config, rubric=rubric, repo_root=real_repo_root, ringer_root=Path("/ringer"),
            state_root=state_root, run=runner,
        )
        orchestrator._run_round = lambda round_no, state: {
            "round": round_no, "score_total": 80, "status": "host_verification_failed",
            "commands_evidence": [], "last_confirmed_deductions": [], "regrade_ok": False, "regrade_failures": [],
        }
        code = orchestrator.run_loop(resume=False)

        assert code == 1
        state = run_loop.load_state(state_root)
        assert state["status"] == "blocked"
        # Exactly the two integration-worktree-creation calls -- both against real_repo_root, and
        # nothing resembling an apply/status call ever issued afterward.
        assert len(runner.calls) == 2
        for cmd, _kwargs in runner.calls:
            assert str(real_repo_root) in cmd
        assert "apply" not in [c[0][4] if len(c[0]) > 4 else None for c in runner.calls]


# --------------------------------------------------------------------------
# run_loop.py -- defect 3: success export to the main checkout
# --------------------------------------------------------------------------


class TestExportSuccess:
    def _orchestrator(self, tmp_path, *, run):
        config = {
            "run_name": "photo-curator-100", "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800, "rubric_path": "ringer-100/rubric-v1.json",
            "max_rounds": 10, "plateau_rounds": 2,
        }
        rubric = _rubric_dict(REAL_RUBRIC_PATH)
        repo_root = tmp_path / "main-checkout"
        state_root = repo_root / "ringer-100" / "state"
        orchestrator = run_loop.LoopOrchestrator(
            config=config, rubric=rubric, repo_root=repo_root, ringer_root=Path("/ringer"),
            state_root=state_root, run=run,
        )
        orchestrator.integration_worktree = tmp_path / "worktree"
        return orchestrator

    def test_export_applies_the_full_cumulative_patch_once(self, tmp_path):
        patch_text = _diff_for("engine/delete/delete_service.py")
        runner = SequenceRunner(
            [
                FakeProc(returncode=0, stdout=patch_text),  # git diff --binary (worktree)
                FakeProc(returncode=0, stdout=""),  # git status --porcelain (main checkout, clean)
                FakeProc(returncode=0),  # git apply --check (main checkout)
                FakeProc(returncode=0),  # git apply (main checkout)
            ]
        )
        orchestrator = self._orchestrator(tmp_path, run=runner)
        state = {"all_owned_files": ["engine/delete/delete_service.py"], "all_declared_fix_tests": []}
        orchestrator._export_success(state)

        final_patch_path = orchestrator.state_root / "final-cumulative.patch"
        assert final_patch_path.read_text(encoding="utf-8") == patch_text
        assert runner.calls[0][0][:5] == ["git", "-C", str(orchestrator.integration_worktree), "diff", "--binary"]
        assert runner.calls[2][0][3:6] == ["apply", "--check", str(final_patch_path)]
        assert runner.calls[3][0][3:5] == ["apply", str(final_patch_path)]
        assert str(orchestrator.repo_root) in runner.calls[2][0]

    def test_export_skips_apply_when_diff_is_empty(self, tmp_path):
        runner = SequenceRunner([FakeProc(returncode=0, stdout="")])
        orchestrator = self._orchestrator(tmp_path, run=runner)
        orchestrator._export_success({"all_owned_files": [], "all_declared_fix_tests": []})
        assert len(runner.calls) == 1  # only the diff call; no status/apply

    def test_export_rejects_patch_touching_files_outside_owned_files(self, tmp_path):
        patch_text = _diff_for("engine/not_owned.py")
        runner = SequenceRunner([FakeProc(returncode=0, stdout=patch_text)])
        orchestrator = self._orchestrator(tmp_path, run=runner)
        with pytest.raises(run_loop.LoopError, match="rejected by validate_patch"):
            orchestrator._export_success({"all_owned_files": ["engine/other.py"], "all_declared_fix_tests": []})
        assert len(runner.calls) == 1  # never reached assert_clean_repo/apply

    def test_export_refuses_if_main_checkout_is_dirty(self, tmp_path):
        patch_text = _diff_for("engine/delete/delete_service.py")
        runner = SequenceRunner(
            [
                FakeProc(returncode=0, stdout=patch_text),
                FakeProc(returncode=0, stdout=" M some_other_file.py\n"),  # main checkout unexpectedly dirty
            ]
        )
        orchestrator = self._orchestrator(tmp_path, run=runner)
        with pytest.raises(run_loop.LoopError, match="not clean"):
            orchestrator._export_success(
                {"all_owned_files": ["engine/delete/delete_service.py"], "all_declared_fix_tests": []}
            )
        assert len(runner.calls) == 2  # never reached apply_patch


# --------------------------------------------------------------------------
# run_loop.py -- an end-to-end round with no confirmed deductions
# --------------------------------------------------------------------------


class TestRoundIntegration:
    def test_review_only_round_lints_before_running_and_short_circuits(self, rubric_copy):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        config = {
            "run_name": "photo-curator-100",
            "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800,
            "rubric_path": "ringer-100/rubric-v1.json",
        }
        score = _base_score(rubric, rubric_path)
        score["categories"]["performance_scalability"]["score"] = 4
        score["total"] = 94
        score["findings"] = []  # no P0-P2 -> no_confirmed_deductions fast path

        calls_order: list[str] = []

        def fake_run(cmd, **kwargs):
            if cmd[2] == "lint":
                calls_order.append(f"lint:{cmd[3]}")
                return FakeProc(returncode=0)
            if cmd[2] == "run":
                calls_order.append(f"run:{cmd[3]}")
                manifest = json.loads(Path(cmd[3]).read_text(encoding="utf-8"))
                task = manifest["tasks"][0]
                # Mirrors Ringer's real task layout: <manifest workdir>/<task key>/<expect_files[0]>
                # -- never <manifest workdir>/<expect_files[0]> (the task cwd's parent).
                score_path = Path(manifest["workdir"]) / task["key"] / task["expect_files"][0]
                score_path.parent.mkdir(parents=True, exist_ok=True)
                score_path.write_text(json.dumps(score), encoding="utf-8")
                return FakeProc(returncode=0)
            raise AssertionError(f"unexpected command: {cmd}")

        state_root = repo_root / "ringer-100" / "state"
        orchestrator = run_loop.LoopOrchestrator(
            config=config, rubric=rubric, repo_root=repo_root, ringer_root=Path("/ringer"),
            state_root=state_root, run=fake_run,
        )
        orchestrator.integration_worktree = FAKE_WSL_WORKTREE
        result = orchestrator._run_round(1, {"score_history": [], "all_owned_files": [], "all_declared_fix_tests": []})

        assert result["status"] == "no_confirmed_deductions"
        assert result["score_total"] == 94
        review_manifest_path = str(state_root / "round-01" / "review-manifest.json")
        assert calls_order == [f"lint:{review_manifest_path}", f"run:{review_manifest_path}"]

        # The controller (not the worker) copies the worker's own artifact into the canonical
        # round score.json.
        canonical_score_path = state_root / "round-01" / "review" / "score.json"
        assert canonical_score_path.is_file()
        assert json.loads(canonical_score_path.read_text(encoding="utf-8"))["total"] == 94

    def test_run_round_raises_if_integration_worktree_not_set(self, rubric_copy):
        repo_root, rubric_path = rubric_copy
        rubric = _rubric_dict(rubric_path)
        config = {
            "run_name": "photo-curator-100", "worker": {"engine": "claude", "model": "sonnet"},
            "worker_timeout_s": 1800, "rubric_path": "ringer-100/rubric-v1.json",
        }
        orchestrator = run_loop.LoopOrchestrator(
            config=config, rubric=rubric, repo_root=repo_root, ringer_root=Path("/ringer"),
            state_root=repo_root / "ringer-100" / "state", run=SequenceRunner([]),
        )
        with pytest.raises(run_loop.LoopError, match="no integration worktree is set"):
            orchestrator._run_round(1, {"score_history": []})


# --------------------------------------------------------------------------
# verify_windows_undo.py -- pure path guards (no PySide6, no real Recycle Bin)
# --------------------------------------------------------------------------


class TestWindowsVerifierGuards:
    def test_require_windows_rejects_non_windows(self):
        with pytest.raises(verify_windows_undo.GuardError, match="requires Windows"):
            verify_windows_undo.require_windows(platform="linux")

    def test_require_windows_accepts_win32(self):
        verify_windows_undo.require_windows(platform="win32")  # should not raise

    def test_require_isolated_root_accepts_path_under_allowed_base(self, tmp_path):
        state_root = tmp_path / "main-repo" / "ringer-100" / "state"
        good = state_root / "round-01" / "windows-verify"
        resolved = verify_windows_undo.require_isolated_root(good, allowed_base=state_root)
        assert resolved == good.resolve()

    def test_require_isolated_root_rejects_path_outside_allowed_base(self, tmp_path):
        state_root = tmp_path / "main-repo" / "ringer-100" / "state"
        bad = tmp_path / "main-repo" / "outputs" / "windows-verify"
        with pytest.raises(verify_windows_undo.GuardError, match="must be under"):
            verify_windows_undo.require_isolated_root(bad, allowed_base=state_root)

    @pytest.mark.parametrize("fragment", ["Pictures", "Photos", "Desktop", "Documents", "OneDrive", "DCIM"])
    def test_require_isolated_root_rejects_personal_media_names(self, tmp_path, fragment):
        state_root = tmp_path / "main-repo" / "ringer-100" / "state"
        bad = state_root / fragment / "round-01"
        with pytest.raises(verify_windows_undo.GuardError, match="personal media location"):
            verify_windows_undo.require_isolated_root(bad, allowed_base=state_root)

    def test_require_generated_fixture_rejects_path_outside_isolated_root(self, tmp_path):
        isolated_root = tmp_path / "isolated"
        outside = tmp_path / "elsewhere" / "photo.jpg"
        with pytest.raises(verify_windows_undo.GuardError, match="outside the isolated artifact root"):
            verify_windows_undo.require_generated_fixture(outside, isolated_root=isolated_root)

    def test_require_generated_fixture_accepts_path_inside_isolated_root(self, tmp_path):
        isolated_root = tmp_path / "isolated"
        inside = isolated_root / "copied-photos" / "a.jpg"
        result = verify_windows_undo.require_generated_fixture(inside, isolated_root=isolated_root)
        assert result == inside.resolve()

    def test_main_refuses_round_dir_outside_state_without_creating_it(self, tmp_path, capsys):
        repo_root = tmp_path / "repo"
        outside_dir = repo_root / "not-state" / "round-01"
        code = verify_windows_undo.main(["--round-dir", str(outside_dir), "--repo-root", str(repo_root)])
        assert code == 2
        assert not outside_dir.exists()
        err = capsys.readouterr().err
        assert "REFUSED" in err

    def test_main_refuses_on_non_windows_before_touching_filesystem(self, tmp_path, monkeypatch, capsys):
        repo_root = tmp_path / "repo"
        round_dir = repo_root / "ringer-100" / "state" / "round-01" / "windows-verify"
        monkeypatch.setattr(sys, "platform", "linux")
        code = verify_windows_undo.main(["--round-dir", str(round_dir), "--repo-root", str(repo_root)])
        assert code == 2
        assert not round_dir.exists()

    # --- defect 6: decoupled import root (integration worktree) vs isolation boundary (main state root)

    def test_state_root_boundary_is_independent_of_repo_root(self, tmp_path, monkeypatch, capsys):
        """The round dir must be confined under the MAIN checkout's state root even when
        --repo-root points at a completely separate integration worktree used only for
        imports."""
        main_state_root = tmp_path / "main-repo" / "ringer-100" / "state"
        integration_worktree = tmp_path / "worktree"
        round_dir_inside_main_state = main_state_root / "round-01" / "windows-verify"

        monkeypatch.setattr(sys, "platform", "linux")  # refuses before filesystem/import work either way
        code = verify_windows_undo.main(
            [
                "--round-dir", str(round_dir_inside_main_state),
                "--repo-root", str(integration_worktree),
                "--state-root", str(main_state_root),
            ]
        )
        assert code == 2  # blocked on require_windows, not on the isolation boundary
        err = capsys.readouterr().err
        assert "requires Windows" in err

    def test_round_dir_under_worktree_state_is_rejected_when_state_root_is_main_repo(self, tmp_path):
        """A round dir nested under the integration worktree's own (nonexistent) state
        directory must NOT satisfy the boundary check when --state-root pins it to the main
        checkout instead."""
        main_state_root = tmp_path / "main-repo" / "ringer-100" / "state"
        integration_worktree = tmp_path / "worktree"
        round_dir_inside_worktree = integration_worktree / "ringer-100" / "state" / "round-01" / "windows-verify"

        with pytest.raises(verify_windows_undo.GuardError, match="must be under"):
            verify_windows_undo.require_isolated_root(round_dir_inside_worktree, allowed_base=main_state_root)
