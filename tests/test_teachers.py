from pathlib import Path

from engine.cli.main import run_pipeline


def test_lower_grades_have_teachers_for_sciences() -> None:
    root = Path(__file__).resolve().parents[1]
    csv, _, _ = run_pipeline(root)
    lines = [l for l in csv.splitlines() if l.strip() and not l.startswith("Grade,")]
    # find B1 Science rows and ensure a teacher is assigned
    b1_science = [l for l in lines if l.startswith("B1,") and ",Science," in l]
    assert b1_science, "Expected B1 Science rows"
    for row in b1_science:
        assert row.split(",")[-1] != "", f"Teacher missing in row: {row}"


def test_b9_english_only_wed_fri() -> None:
    root = Path(__file__).resolve().parents[1]
    csv, _, _ = run_pipeline(root)
    rows = [l for l in csv.splitlines() if l.startswith("B9,") and ",English," in l]
    for r in rows:
        assert any(
            day in r for day in [",Wednesday,", ",Friday,"]
        ), f"B9 English on invalid day: {r}"
