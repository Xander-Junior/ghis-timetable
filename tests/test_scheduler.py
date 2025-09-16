from pathlib import Path

from engine.cli.main import run_pipeline


def test_pipeline_runs() -> None:
    root = Path(__file__).resolve().parents[1]
    csv, validation, audit = run_pipeline(root)
    assert len(csv.strip()) > 0
    assert "clash_count" in validation
