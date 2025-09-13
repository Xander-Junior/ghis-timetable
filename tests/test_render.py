from pathlib import Path
from engine.cli.main import run_pipeline


def test_csv_blocks_smoke(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    csv, validation, audit = run_pipeline(root)
    assert "Grade,Day,PeriodStart,PeriodEnd,Subject,Teacher" in csv
    assert "Extra Curricular" in csv
    assert "Break," in csv
    assert "Lunch," in csv

