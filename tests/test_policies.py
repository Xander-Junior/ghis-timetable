from pathlib import Path

from engine.cli.main import run_pipeline


def test_ucmas_policy_no_same_slot_and_gap() -> None:
    root = Path(__file__).resolve().parents[1]
    _, validation, _ = run_pipeline(root)
    assert "ucmas_same_slot" not in validation
    assert "ucmas_gap" not in validation


def test_no_teacherless_teaching_cells_for_core() -> None:
    root = Path(__file__).resolve().parents[1]
    csv, _, _ = run_pipeline(root)
    for line in csv.splitlines():
        if not line or line.startswith("Grade,"):
            continue
        parts = line.split(",")
        grade, day, start, end, subject, teacher = parts
        if subject in {"Break", "Lunch", "Extra Curricular", "UCMAS", "P.E."}:
            continue
        assert teacher != "", f"Missing teacher for teaching subject: {line}"
