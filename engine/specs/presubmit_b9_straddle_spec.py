from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.presubmit_check import read_schedule_csv, validate_rows  # type: ignore

GOLDEN = ROOT / "engine" / "specs" / "golden"


def _run(name: str):
    p = GOLDEN / name
    rows = read_schedule_csv(p)
    return validate_rows(rows)


def test_b9_wed_double_straddle_ok() -> None:
    errors, metrics, globals_ = _run("b9_english_wed_double_straddle_ok.csv")
    assert not any("B9_ENGLISH_WED_DOUBLE_REQUIRED" == s for s in globals_), globals_


def test_b9_fri_double_straddle_ok() -> None:
    errors, metrics, globals_ = _run("b9_english_fri_double_straddle_ok.csv")
    assert not any("B9_ENGLISH_FRI_DOUBLE_REQUIRED" == s for s in globals_), globals_
    assert not any("B9_ENGLISH_FRI_DOUBLE_FORBID_T9" == s for s in globals_), globals_

