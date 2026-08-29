"""Contract check for review item 5 (honest skip/error reasons).

The completion dialog must report every real skip reason with its count and must
never assert a cause the results do not support -- including the inverse case: a
genuine "changed since indexing" skip must still be reported, so the fix cannot
be "delete the string".

Deliberately tolerant on formatting (any layout, any headings, case-insensitive)
and strict on substance.

Run it from anywhere with the project's Windows interpreter:
    .venv\\Scripts\\python.exe scripts/check_honest_reasons.py

Exit 0 = every scenario held. Non-zero prints exactly which scenario broke.

This check deliberately re-implements the contract independently of tests/, so a
change that quietly weakens a unit test still fails here. It was written to fail
against the pre-fix code and is kept as a regression guard. It is not part of the
pytest suite.

If you ever use it to gate an agent that owns repo files, run a COPY from outside
that agent's worktree -- an agent that can edit its own check has no check.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES: list[str] = []
PASSES: list[str] = []


def scenario(name):
    def wrap(fn):
        try:
            fn()
        except AssertionError as exc:
            FAILURES.append(f"{name}: {exc}")
        except Exception:
            FAILURES.append(f"{name}: raised unexpectedly\n{traceback.format_exc()}")
        else:
            PASSES.append(name)
        return fn

    return wrap


from app.ui.main_window import format_delete_summary  # noqa: E402
from engine.delete.delete_service import TrashResult  # noqa: E402

CHANGED = "file changed since indexing; not deleted"
MISSING_KEEP = "keep copy is missing from disk"
OUTSIDE = "path is outside every added folder"
PERMISSION = "[WinError 5] Access is denied"


def ok(name: str) -> TrashResult:
    return TrashResult(Path(name), True, Path(r"C:\$Recycle.Bin\$R" + name))


def unlogged(name: str) -> TrashResult:
    return TrashResult(
        Path(name),
        True,
        Path(r"C:\$Recycle.Bin\$R" + name),
        error="file is in the Recycle Bin but has no Undo Delete entry: disk full",
        undo_logged=False,
    )


def skipped(name: str, error: str | None) -> TrashResult:
    return TrashResult(Path(name), False, error=error)


def line_with(summary: str, needle: str) -> str | None:
    """Return the first line mentioning *needle* (case-insensitive), else None."""
    for line in summary.splitlines():
        if needle.casefold() in line.casefold():
            return line
    return None


@scenario("item 5: with nothing skipped, no skip cause is asserted at all")
def _no_false_cause():
    summary = format_delete_summary([ok("a.jpg"), ok("b.jpg")])
    assert "changed since indexing" not in summary.casefold(), (
        "the summary still claims files were changed since indexing even though "
        f"nothing was skipped:\n{summary}"
    )
    assert "2" in summary, f"the two recycled files are not reported:\n{summary}"


@scenario("item 5: each real skip reason is reported with its own count")
def _real_reasons_reported():
    results = [
        ok("a.jpg"),
        skipped("b.jpg", MISSING_KEEP),
        skipped("c.jpg", OUTSIDE),
        skipped("d.jpg", OUTSIDE),
    ]
    summary = format_delete_summary(results)
    for reason, count in ((MISSING_KEEP, 1), (OUTSIDE, 2)):
        line = line_with(summary, reason)
        assert line is not None, f"reason {reason!r} is not reported at all:\n{summary}"
        assert str(count) in line, (
            f"reason {reason!r} is reported without its count of {count}: {line!r}"
        )
    assert "changed since indexing" not in summary.casefold(), (
        f"a cause no result actually reported is being claimed:\n{summary}"
    )


@scenario("item 5: a genuine changed-since-indexing skip IS still reported")
def _genuine_change_reported():
    summary = format_delete_summary([ok("a.jpg"), skipped("b.jpg", CHANGED)])
    assert line_with(summary, "changed since indexing") is not None, (
        f"the real reason was dropped instead of reported:\n{summary}"
    )


@scenario("item 5: an OS error reason survives verbatim rather than being generalized")
def _os_error_reported():
    summary = format_delete_summary([skipped("a.jpg", PERMISSION)])
    assert line_with(summary, "access is denied") is not None, (
        f"the real OS error was replaced with a generic explanation:\n{summary}"
    )


@scenario("item 5: a skip with no recorded reason is surfaced, not silently dropped")
def _unknown_reason_surfaced():
    results = [ok("a.jpg"), skipped("b.jpg", None)]
    summary = format_delete_summary(results)
    digits = [line for line in summary.splitlines() if "1" in line]
    assert digits, f"the unexplained skip is not counted anywhere:\n{summary}"
    assert len(summary.strip()) > 0
    lowered = summary.casefold()
    assert any(word in lowered for word in ("unknown", "unspecified", "no reason", "unrecorded")), (
        "a skip with no recorded error must be reported as an unknown reason rather than "
        f"omitted or attributed to a cause:\n{summary}"
    )


@scenario("item 5: recycled, unlogged and skipped counts are all reported and add up")
def _counts_add_up():
    results = [
        ok("a.jpg"),
        ok("b.jpg"),
        unlogged("c.jpg"),
        skipped("d.jpg", OUTSIDE),
    ]
    summary = format_delete_summary(results)
    trashed_line = line_with(summary, "recycle bin") or line_with(summary, "moved")
    assert trashed_line is not None, f"the recycled count is not reported:\n{summary}"
    assert "3" in trashed_line, (
        f"3 files really were recycled (one of them unlogged); reported as: {trashed_line!r}"
    )
    undo_line = line_with(summary, "undo")
    assert undo_line is not None and "1" in undo_line, (
        "the file that is in the Recycle Bin but has no undo entry must still be called out:\n"
        f"{summary}"
    )
    assert line_with(summary, OUTSIDE) is not None, f"the skip reason is missing:\n{summary}"


for name in PASSES:
    print(f"PASS  {name}")
for failure in FAILURES:
    print(f"FAIL  {failure}")

if FAILURES:
    print(f"\n{len(FAILURES)} of {len(PASSES) + len(FAILURES)} contract scenarios FAILED")
    raise SystemExit(1)
print(f"\nall {len(PASSES)} contract scenarios passed")
