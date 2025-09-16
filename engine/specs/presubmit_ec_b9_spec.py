from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.presubmit_check import read_schedule_csv, validate_rows  # type: ignore

GOLDEN = ROOT / "engine" / "specs" / "golden"


def _run(csv_name: str):
    p = GOLDEN / csv_name
    rows = read_schedule_csv(p)
    return validate_rows(rows)


def test_ec_friday_t9_ok():
    errors, metrics, globals_ = _run("ec_friday_t9_ok.csv")
    assert not any("EC_FRIDAY_T9_REQUIRED" in s for s in globals_), globals_
    assert not any("EC_FORBIDDEN_OUTSIDE_FRI_T9" in s for s in globals_), globals_


def test_ec_friday_t9_bad():
    errors, metrics, globals_ = _run("ec_friday_t9_bad.csv")
    assert any("EC_FORBIDDEN_OUTSIDE_FRI_T9" in s for s in globals_), globals_


def test_b9_openrev_friday_missing():
    errors, metrics, globals_ = _run("b9_openrev_friday_t9_missing.csv")
    assert any("B9_OPENREV_FRIDAY_T9_REQUIRED" in s for s in globals_), globals_


def test_b9_openrev_distinct_bad():
    errors, metrics, globals_ = _run("b9_openrev_distinct_bad.csv")
    assert any("B9_OPENREV_DISTINCT_DAYS" in s for s in globals_), globals_


def test_b9_english_fri_double_bad():
    errors, metrics, globals_ = _run("b9_english_fri_double_bad.csv")
    assert any("B9_ENGLISH_FRI_DOUBLE_FORBID_T9" in s for s in globals_) or any(
        "B9_ENGLISH_FRI_DOUBLE_REQUIRED" in s for s in globals_
    ), globals_

