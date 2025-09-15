from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.presubmit_check import read_schedule_csv, validate_rows  # type: ignore

GOLDEN = ROOT / "engine" / "specs" / "golden"


def test_b9_openrev_ok() -> None:
    p = GOLDEN / "b9_openrev_ok.csv"
    rows = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(rows)
    # No OpenRevision errors; English rules satisfied
    assert not any(s.startswith("B9_OPENREV_") for s in global_errors), global_errors
    assert not any(s.startswith("B9_ENGLISH_") for s in global_errors), global_errors
    # Teacherless: ensure cells have no teacher (we set it empty in golden)
    assert all(r.teacher == "" for r in rows if r.subject == "OpenRevision")


def _write_subjects_toml(text: str) -> str:
    cfg = ROOT / "configs" / "subjects.toml"
    orig = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
    cfg.write_text(text, encoding="utf-8")
    return orig


def test_b9_openrev_missing_counts(tmp_path: Path) -> None:
    # Base golden has 0; mutate in-memory to simulate 1 and 3
    p = GOLDEN / "b9_openrev_missing.csv"
    rows = read_schedule_csv(p)
    # 0 -> fail
    e0, m0, g0 = validate_rows(rows)
    assert any(s.startswith("B9_OPENREV_COUNT:") for s in g0)

    # 1 -> fail (add one OpenRevision row)
    rows1 = list(rows)
    rows1.append(
        type(rows1[0])(
            line_no=9999,
            grade="B9",
            day="Monday",
            start="08:55",
            end="09:50",
            subject="OpenRevision",
            teacher="",
        )
    )
    e1, m1, g1 = validate_rows(rows1)
    assert any(s.startswith("B9_OPENREV_COUNT:") for s in g1)

    # 3 -> fail (add two more OpenRevision rows)
    rows3 = list(rows1)
    rows3.append(
        type(rows1[0])(
            line_no=10000,
            grade="B9",
            day="Thursday",
            start="11:25",
            end="12:20",
            subject="OpenRevision",
            teacher="",
        )
    )
    rows3.append(
        type(rows1[0])(
            line_no=10001,
            grade="B9",
            day="Tuesday",
            start="08:00",
            end="08:55",
            subject="OpenRevision",
            teacher="",
        )
    )
    e3, m3, g3 = validate_rows(rows3)
    assert any(s.startswith("B9_OPENREV_COUNT:") for s in g3)


def test_b9_openrev_same_day_enforced_when_distinct_days_on() -> None:
    # Toggle distinct_days.B9 = 2 and expect failure on same-day placement
    cfg_path = ROOT / "configs" / "subjects.toml"
    original = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    try:
        cfg_path.write_text(
            (
                "[subjects.OpenRevision]\n"
                "weekly_min.B9 = 2\n"
                "weekly_max.B9 = 2\n"
                "distinct_days.B9 = 2\n"
                'prefer_days = ["Monday", "Tuesday", "Thursday"]\n'
                "teacher_required = false\n"
            ),
            encoding="utf-8",
        )
        p = GOLDEN / "b9_openrev_same_day.csv"
        rows = read_schedule_csv(p)
        errors, metrics, global_errors = validate_rows(rows)
        assert any(s.startswith("B9_OPENREV_DISTINCT_DAYS:") for s in global_errors), global_errors
    finally:
        cfg_path.write_text(original, encoding="utf-8")
