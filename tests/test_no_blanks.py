from __future__ import annotations

import json
from pathlib import Path

from engine.cli.main import run_pipeline


def test_no_blanks_and_clash_free(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    csv, _, _ = run_pipeline(root)

    # No blank teaching cells: every row must have a subject (Break/Lunch/Extra Curricular allowed)
    lines = [l for l in csv.splitlines() if l.strip()]
    # skip header lines
    data_lines = [l for l in lines if not l.startswith("Grade,Day,")]
    blanks: list[str] = []
    for row in data_lines:
        parts = row.split(",")
        # Expected 6 columns
        if len(parts) < 6:
            continue
        subject = parts[4].strip()
        # Break/Lunch/Extra Curricular are valid non-teaching or fixed teaching labels
        if subject == "":
            blanks.append(row)
    assert not blanks, f"Found blank timetable cells: {blanks[:5]} (and more)"

    # Clash-free check
    vpath = root / "outputs" / "validation.json"
    assert vpath.exists(), "validation.json missing; pipeline may not have run"
    report = json.loads(vpath.read_text(encoding="utf-8"))
    assert report.get("clash_count", 1) == 0, f"clash_count != 0: {report.get('clash_count')}"
