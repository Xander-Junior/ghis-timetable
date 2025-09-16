from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.presubmit_check import read_schedule_csv, validate_rows  # type: ignore

GOLDEN = ROOT / "engine" / "specs" / "golden"


def _write_rows(path: Path, rows: Iterable[tuple[str, str, str, str, str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Grade", "Day", "PeriodStart", "PeriodEnd", "Subject", "Teacher"])
        for r in rows:
            w.writerow(list(r))


def test_jhs_english_ok(tmp_path: Path) -> None:
    # B7 and B8: Wed/Fri Bright; Mon/Tue/Thu Harriet; distinct days=4
    rows = [
        ("B7", "Wednesday", "10:00", "10:45", "English", "Sir Bright Dey"),
        ("B7", "Friday", "09:15", "10:00", "English", "Sir Bright Dey"),
        ("B7", "Monday", "08:30", "09:15", "English", "Harriet Akasraku"),
        ("B7", "Thursday", "11:30", "12:15", "English", "Harriet Akasraku"),
        ("B8", "Wednesday", "11:30", "12:15", "English", "Sir Bright Dey"),
        ("B8", "Friday", "10:00", "10:45", "English", "Sir Bright Dey"),
        ("B8", "Monday", "09:15", "10:00", "English", "Harriet Akasraku"),
        ("B8", "Tuesday", "12:15", "13:00", "English", "Harriet Akasraku"),
        # B9: two doubles (Wed adjacent, Fri T8+T9)
        ("B9", "Wednesday", "11:25", "12:20", "English", "Sir Bright Dey"),
        ("B9", "Wednesday", "12:20", "13:15", "English", "Sir Bright Dey"),
        ("B9", "Friday", "13:30", "14:25", "English", "Sir Bright Dey"),
        ("B9", "Friday", "14:45", "15:20", "English", "Sir Bright Dey"),
    ]
    p = tmp_path / "jhs_english_ok.csv"
    _write_rows(p, rows)
    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)
    assert not errors and not global_errors


def test_jhs_english_wrong_days(tmp_path: Path) -> None:
    # B7 missing Friday English, should fail
    rows = [
        ("B7", "Wednesday", "10:00", "10:45", "English", "Sir Bright Dey"),
        ("B7", "Monday", "08:30", "09:15", "English", "Harriet Akasraku"),
        ("B7", "Tuesday", "11:30", "12:15", "English", "Harriet Akasraku"),
        ("B7", "Thursday", "12:15", "13:00", "English", "Harriet Akasraku"),
        ("B8", "Wednesday", "10:00", "10:45", "English", "Sir Bright Dey"),
        ("B8", "Friday", "09:15", "10:00", "English", "Sir Bright Dey"),
        ("B8", "Monday", "08:30", "09:15", "English", "Harriet Akasraku"),
        ("B8", "Tuesday", "11:30", "12:15", "English", "Harriet Akasraku"),
        ("B9", "Wednesday", "10:00", "10:45", "English", "Sir Bright Dey"),
        ("B9", "Friday", "14:45", "15:20", "English", "Sir Bright Dey"),
        ("B9", "Monday", "08:30", "09:15", "English", "Sir Bright Dey"),
        ("B9", "Tuesday", "09:15", "10:00", "English", "Sir Bright Dey"),
    ]
    p = tmp_path / "jhs_english_wrong_days.csv"
    _write_rows(p, rows)
    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)
    assert any("JHS_ENGLISH_FRI_SIR_BRIGHT_FAIL:B7" in s for s in global_errors)


def test_jhs_english_wrong_teacher(tmp_path: Path) -> None:
    # Harriet on Friday or Bright on Monday for B7/B8 should fail
    rows = [
        ("B7", "Wednesday", "10:00", "10:45", "English", "Sir Bright Dey"),
        ("B7", "Friday", "09:15", "10:00", "English", "Harriet Akasraku"),
        ("B7", "Monday", "08:30", "09:15", "English", "Sir Bright Dey"),
        ("B7", "Thursday", "11:30", "12:15", "English", "Harriet Akasraku"),
    ]
    p = tmp_path / "jhs_english_wrong_teacher.csv"
    _write_rows(p, rows)
    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)
    has_cell_forbid = any(
        "JHS_ENGLISH_BRIGHT_FORBIDDEN_MON_TUE_THU" in ",".join(v) for v in errors.values()
    ) or any("JHS_ENGLISH_HARRIET_FORBIDDEN_WED_FRI" in ",".join(v) for v in errors.values())
    assert has_cell_forbid


def test_pe_bands_bad(tmp_path: Path) -> None:
    # B1 is supposed to have Friday P1=P.E.; here it's not
    rows = [
        ("B1", "Friday", "08:30", "09:15", "Math", "T1"),
        ("B1", "Friday", "09:15", "10:00", "P.E.", "PE1"),
    ]
    p = tmp_path / "pe_bands_bad.csv"
    _write_rows(p, rows)
    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)
    assert any("PE_BAND_SLOT_NOT_PE" in ",".join(v) for v in errors.values()) or any(
        s.startswith("PE_BAND_MISSING:B1:P1") for s in global_errors
    )


def test_twi_violation_jhs(tmp_path: Path) -> None:
    rows = [
        ("B7", "Tuesday", "10:00", "10:45", "Twi", "TW1"),
    ]
    p = tmp_path / "twi_violation_jhs.csv"
    _write_rows(p, rows)
    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)
    assert any("TWI_WINDOW_VIOLATION" in ",".join(v) for v in errors.values())


def test_b9_fri_t9_not_english(tmp_path: Path) -> None:
    # Explicitly violate B9 Friday T9 English pin
    rows = [
        ("B9", "Friday", "14:45", "15:20", "Extra Curricular", "EC1"),
    ]
    p = tmp_path / "b9_fri_t9_not_english.csv"
    _write_rows(p, rows)
    parsed = read_schedule_csv(p)
    errors, metrics, global_errors = validate_rows(parsed)
    flat = {c for cs in errors.values() for c in cs}
    assert "B9_FRI_T9_ENGLISH_REQUIRED" in flat or "EC_FORBIDDEN_T9_B9_FRI" in flat
