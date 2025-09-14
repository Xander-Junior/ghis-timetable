from __future__ import annotations

from pathlib import Path
import sys

# Ensure repository root on path for `scripts` import
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from scripts.presubmit_check import read_schedule_csv, validate_rows, main as presubmit_main


# Time slots used in tests (from data/structure.json)
T = {
    "T1": ("08:00", "08:55"),
    "T2": ("08:55", "09:50"),
    "T3": ("09:50", "10:45"),
    "T4": ("10:45", "11:25"),  # Break
    "T5": ("11:25", "12:20"),
    "T6": ("12:20", "13:15"),
    "T7": ("13:15", "13:30"),  # Lunch
    "T8": ("13:30", "14:25"),
    "T9": ("14:45", "15:20"),
}


def write_csv(path: Path, rows: list[tuple[str, str, str, str, str, str]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("Grade,Day,PeriodStart,PeriodEnd,Subject,Teacher\n")
        for r in rows:
            f.write(",".join(r) + "\n")


def test_good_schedule_passes(tmp_path: Path) -> None:
    # B9 has English on Wed and Fri; Friday T9 English; Twi only on Wed/Fri
    rows = [
        ("B9", "Wednesday", *T["T2"], "Twi", "TW1"),
        ("B9", "Wednesday", *T["T5"], "English", "EN1"),
        ("B9", "Friday", *T["T3"], "Twi", "TW1"),
        ("B9", "Friday", *T["T8"], "Science", "SC1"),
        ("B9", "Friday", *T["T9"], "English", "EN2"),
    ]
    p = tmp_path / "good.csv"
    write_csv(p, rows)

    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)

    assert not errors and not global_errors
    # Quick check metrics exist
    # Minimal required metrics present
    for k in ("adjacency_violations", "same_slot_repeat_score", "fallback_usage"):
        assert k in metrics

    # Integration: CLI returns 0
    assert presubmit_main([str(p)]) == 0


def test_illegal_twi_day(tmp_path: Path) -> None:
    rows = [
        ("B8A", "Monday", *T["T2"], "Twi", "TW1"),
    ]
    p = tmp_path / "bad_twi.csv"
    write_csv(p, rows)

    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)
    # Find the only line's errors
    assert len(errors) == 1
    codes = list(errors.values())[0]
    assert "TWI_WINDOW_VIOLATION" in codes


def test_missing_b9_english_on_friday_or_wrong_t9(tmp_path: Path) -> None:
    # Has English on Wednesday but Friday T9 is Extra Curricular -> both day-missing and T9 failure
    rows = [
        ("B9", "Wednesday", *T["T5"], "English", "EN1"),
        ("B9", "Friday", *T["T9"], "Extra Curricular", "EC1"),
    ]
    p = tmp_path / "bad_b9_fri.csv"
    write_csv(p, rows)

    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)

    # Should have a per-row T9 English requirement error
    flat_codes = {c for codes in errors.values() for c in codes}
    assert "B9_FRI_T9_ENGLISH_REQUIRED" in flat_codes
    assert "EC_FORBIDDEN_T9_B9_FRI" in flat_codes
    # And also missing Friday English day-level error
    assert any(e.startswith("B9_ENGLISH_DAY_MISSING:Friday") for e in global_errors)


def test_adjacency_violations_b9_exception(tmp_path: Path) -> None:
    # B8 has a double-block Math on Monday (counts as 1 violation)
    # B9 has exactly one English double-block in the week (allowed, subtract 1)
    rows = [
        ("B8A", "Monday", *T["T2"], "Math", "M1"),
        ("B8A", "Monday", *T["T3"], "Math", "M1"),
        ("B9", "Wednesday", *T["T5"], "English", "E1"),
        ("B9", "Wednesday", *T["T6"], "English", "E1"),
        ("B9", "Friday", *T["T9"], "English", "E2"),  # satisfy Friday English
    ]
    p = tmp_path / "adjacency.csv"
    write_csv(p, rows)

    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)

    # No hard failures expected from adjacency alone
    assert not errors and not global_errors
    # total_adjacent = 2 (B8 Math + B9 English) minus 1 allowed -> 1
    assert metrics["adjacency_violations"] == 1


def test_strict_ok_thresholds(tmp_path: Path) -> None:
    # No fallbacks, small adjacency, no repeats -> passes strict
    rows = [
        ("B7A", "Monday", *T["T2"], "Math", "M1"),
        ("B7A", "Monday", *T["T3"], "Science", "S1"),
        ("B9", "Friday", *T["T9"], "English", "E2"),
        ("B9", "Wednesday", *T["T5"], "English", "E1"),
    ]
    p = tmp_path / "strict_ok.csv"
    write_csv(p, rows)
    assert presubmit_main([str(p), "--strict"]) == 0


def test_strict_fails_on_fallback(tmp_path: Path) -> None:
    rows = [
        ("B7A", "Monday", *T["T2"], "Supervised Study", ""),
        ("B9", "Friday", *T["T9"], "English", "E2"),
        ("B9", "Wednesday", *T["T5"], "English", "E1"),
    ]
    p = tmp_path / "strict_fallback.csv"
    write_csv(p, rows)
    assert presubmit_main([str(p), "--strict"]) != 0


def test_strict_adj_threshold_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Create one adjacency and set MAX_ADJ=0 -> should fail
    rows = [
        ("B8A", "Monday", *T["T2"], "Math", "M1"),
        ("B8A", "Monday", *T["T3"], "Math", "M1"),
        ("B9", "Friday", *T["T9"], "English", "E2"),
        ("B9", "Wednesday", *T["T5"], "English", "E1"),
    ]
    p = tmp_path / "strict_adj.csv"
    write_csv(p, rows)
    monkeypatch.setenv("MAX_ADJ", "0")
    assert presubmit_main([str(p), "--strict"]) != 0


def test_strict_same_slot_threshold_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same subject in same slot across multiple days -> score 1; set MAX_SAME_SLOT=0 -> fail
    rows = [
        ("B7A", "Monday", *T["T2"], "Math", "M1"),
        ("B7A", "Tuesday", *T["T2"], "Math", "M1"),
        # Ensure B9 Friday T9 English requirement is satisfied
        ("B9", "Friday", *T["T9"], "English", "E2"),
        ("B9", "Wednesday", *T["T5"], "English", "E1"),
    ]
    p = tmp_path / "strict_same_slot.csv"
    write_csv(p, rows)
    monkeypatch.setenv("MAX_SAME_SLOT", "0")
    assert presubmit_main([str(p), "--strict"]) != 0


def test_strict_per_grade_adj_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # B9 exceeds per-grade adjacency cap while others are under global cap
    rows = [
        # B9: English double (allowed subtract 1), plus two non-English doubles => 3 total -> 2 after exception
        ("B9", "Wednesday", *T["T5"], "English", "E1"),
        ("B9", "Wednesday", *T["T6"], "English", "E1"),
        ("B9", "Monday", *T["T2"], "Math", "M1"),
        ("B9", "Monday", *T["T3"], "Math", "M1"),
        ("B9", "Friday", *T["T2"], "Science", "S1"),
        ("B9", "Friday", *T["T3"], "Science", "S1"),
        ("B9", "Friday", *T["T9"], "English", "E2"),  # ensure Friday T9 English
        # Another grade with no adjacency issues
        ("B7A", "Tuesday", *T["T2"], "Math", "M1"),
        ("B7A", "Wednesday", *T["T3"], "Science", "S1"),
    ]
    p = tmp_path / "per_grade_adj.csv"
    write_csv(p, rows)
    # Set per-grade cap for B9 to 1; globals remain higher (default 3)
    monkeypatch.setenv("MAX_ADJ_B9", "1")
    rc = presubmit_main([str(p), "--strict"])
    assert rc != 0


def test_strict_per_grade_same_slot_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Only B8A repeats same subject in same slot across days
    rows = [
        ("B8A", "Monday", *T["T2"], "Math", "M1"),
        ("B8A", "Tuesday", *T["T2"], "Math", "M1"),  # repeat in same slot
        # B9 satisfy Friday/Wednesday English requirements
        ("B9", "Friday", *T["T9"], "English", "E2"),
        ("B9", "Wednesday", *T["T5"], "English", "E1"),
    ]
    p = tmp_path / "per_grade_same_slot.csv"
    write_csv(p, rows)
    # Per-grade cap zero for B8A, so one repeat triggers failure
    monkeypatch.setenv("MAX_SAME_SLOT_B8A", "0")
    rc = presubmit_main([str(p), "--strict"])
    assert rc != 0
