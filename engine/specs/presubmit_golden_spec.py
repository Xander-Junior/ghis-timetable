from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import presubmit_check as pc  # type: ignore

GOLDEN = ROOT / "engine" / "specs" / "golden"


def _strict_env(
    max_adj: str = "2", max_same: str = "6", per_grade: dict[str, str] | None = None
) -> dict[str, str]:
    env = dict(os.environ)
    env["MAX_ADJ"] = str(max_adj)
    env["MAX_SAME_SLOT"] = str(max_same)
    for k, v in (per_grade or {}).items():
        env[k] = str(v)
    return env


def _run_strict(csv_path: Path, env_overrides: dict[str, str] | None = None) -> dict[str, object]:
    # Mirror the presubmit strict semantics using the library API
    rows = pc.read_schedule_csv(csv_path)
    errors_by_line, metrics, global_errors = pc.validate_rows(rows)

    env = _strict_env()
    if env_overrides:
        env.update(env_overrides)

    strict_fail_reasons: list[str] = []

    # Fallback usage forbidden in strict
    fb = metrics.get("fallback_usage", metrics.get("fallback_supervised", 0))
    if isinstance(fb, int) and fb > 0:
        strict_fail_reasons.append("STRICT_FALLBACK_FORBIDDEN:fallback_usage>0")

    # Per-grade thresholds (per-grade overrides take precedence)
    max_adj = int(env.get("MAX_ADJ", "2"))
    max_same = int(env.get("MAX_SAME_SLOT", "6"))
    adj_by_grade = metrics.get("adjacency_by_grade", {}) or {}
    same_by_grade = metrics.get("same_slot_by_grade", {}) or {}

    if isinstance(adj_by_grade, dict):
        for grade, val in adj_by_grade.items():
            cap = int(env.get(f"MAX_ADJ_{grade}", str(max_adj)))
            if int(val) > cap:
                strict_fail_reasons.append(f"STRICT_ADJ_LIMIT:{grade}:{val}>{cap}")

    if isinstance(same_by_grade, dict):
        for grade, val in same_by_grade.items():
            cap = int(env.get(f"MAX_SAME_SLOT_{grade}", str(max_same)))
            if int(val) > cap:
                strict_fail_reasons.append(f"STRICT_SAME_SLOT_LIMIT:{grade}:{val}>{cap}")

    hard_fail = bool(errors_by_line or global_errors)

    return {
        "errors_by_line": errors_by_line,
        "metrics": metrics,
        "global_errors": global_errors,
        "strict_fail_reasons": strict_fail_reasons,
        "hard_fail": hard_fail,
    }


def test_strict_ok_passes() -> None:
    csv_path = GOLDEN / "strict_ok.csv"
    out = _run_strict(csv_path)
    assert not out["hard_fail"], f"Hard fails: {out['errors_by_line'] or out['global_errors']}"
    assert not out["strict_fail_reasons"], f"Strict fails: {out['strict_fail_reasons']}"
    m = out["metrics"]
    # Sanity keys
    for k in ("adjacency_by_grade", "same_slot_by_grade"):
        assert k in m


def _has_code(errors_by_line: dict[int, list[str]], needle: str) -> bool:
    for codes in errors_by_line.values():
        if any(needle in c for c in codes):
            return True
    return False


def test_teacher_conflict_fails() -> None:
    csv_path = GOLDEN / "teacher_conflict.csv"
    out = _run_strict(csv_path)
    assert _has_code(
        out["errors_by_line"], "TEACHER_CONFLICT"
    ), f"Expected TEACHER_CONFLICT, got: {out}"


def test_class_conflict_fails() -> None:
    csv_path = GOLDEN / "class_conflict.csv"
    out = _run_strict(csv_path)
    assert _has_code(
        out["errors_by_line"], "CLASS_CONFLICT"
    ), f"Expected CLASS_CONFLICT, got: {out}"


def test_twi_violation_fails() -> None:
    csv_path = GOLDEN / "twi_violation.csv"
    out = _run_strict(csv_path)
    assert _has_code(
        out["errors_by_line"], "TWI_WINDOW_VIOLATION"
    ), f"Expected TWI_WINDOW_VIOLATION, got: {out}"


def test_b9_fri_t9_not_english_fails() -> None:
    csv_path = GOLDEN / "b9_fri_t9_not_english.csv"
    out = _run_strict(csv_path)
    bad_cell = _has_code(out["errors_by_line"], "B9_FRI_T9_ENGLISH_REQUIRED") or _has_code(
        out["errors_by_line"], "EC_FORBIDDEN_T9_B9_FRI"
    )
    missing_day = any("B9_ENGLISH_DAY_MISSING" in ge for ge in out["global_errors"])  # type: ignore[index]
    assert bad_cell or missing_day, f"Expected B9 Friday T9 enforcement, got: {out}"


def test_fallback_forbidden_in_strict() -> None:
    csv_path = GOLDEN / "fallback_present.csv"
    out = _run_strict(csv_path)
    assert "STRICT_FALLBACK_FORBIDDEN:fallback_usage>0" in out["strict_fail_reasons"], out


def test_per_grade_overrides_trip_strict() -> None:
    # Use strict_ok but set MAX_SAME_SLOT_B9=0 to force failure if B9 has any same-slot repeats > 0
    csv_path = GOLDEN / "strict_ok.csv"
    out = _run_strict(csv_path, env_overrides={"MAX_SAME_SLOT_B9": "0"})
    same_by_grade = out["metrics"].get("same_slot_by_grade", {})  # type: ignore[index]
    if isinstance(same_by_grade, dict) and int(same_by_grade.get("B9", 0)) > 0:
        assert any(
            r.startswith("STRICT_SAME_SLOT_LIMIT:B9") for r in out["strict_fail_reasons"]  # type: ignore[index]
        ), out
