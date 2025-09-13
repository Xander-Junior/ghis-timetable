from pathlib import Path
from engine.cli.main import run_pipeline


def test_validation_report_has_sections() -> None:
    root = Path(__file__).resolve().parents[1]
    csv, validation, audit = run_pipeline(root)
    assert "violations_by_rule" in validation
    assert "unmet_weekly_loads" in validation

