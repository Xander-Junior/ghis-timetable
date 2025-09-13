from __future__ import annotations

import json
import sys
from pathlib import Path

import sys

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from engine.cli.main import run_pipeline


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    # Option A: more search + bigger gap penalty and adjacency/same-time penalties
    csv, _, _ = run_pipeline(
        root,
        max_repairs=30,
        max_swaps=1000,
        penalty_same_time=16,
        penalty_adjacent=6,
        deficit_weight=300,
        relax_electives=False,
    )
    # Check no blanks
    errors: list[str] = []
    grades_with_blanks: set[str] = set()
    for i, line in enumerate(csv.splitlines(), start=1):
        if not line or line.startswith("Grade,Day,"):
            continue
        parts = line.split(",")
        if len(parts) < 6:
            errors.append(f"Malformed CSV row {i}: {line}")
            continue
        subject = parts[4].strip()
        grade = parts[0].strip()
        if subject == "":
            errors.append(f"Blank subject at CSV row {i}: {line}")
            if grade:
                grades_with_blanks.add(grade)

    # Check clash-free
    vpath = root / "outputs" / "validation.json"
    try:
        report = json.loads(vpath.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: Could not read validation.json: {e}")
        return 1
    clash_count = report.get("clash_count", 1)
    if clash_count != 0:
        errors.append(f"clash_count != 0 (got {clash_count})")

    if errors:
        # Option B: relax elective minima by -1 for affected grades only and try again once
        csv2, _, _ = run_pipeline(
            root,
            max_repairs=30,
            max_swaps=1000,
            penalty_same_time=16,
            penalty_adjacent=6,
            deficit_weight=300,
            relax_electives=sorted(grades_with_blanks) if grades_with_blanks else True,
        )
        errors2: list[str] = []
        for i, line in enumerate(csv2.splitlines(), start=1):
            if not line or line.startswith("Grade,Day,"):
                continue
            parts = line.split(",")
            if len(parts) < 6:
                errors2.append(f"Malformed CSV row {i}: {line}")
                continue
            subject = parts[4].strip()
            if subject == "":
                errors2.append(f"Blank subject at CSV row {i}: {line}")
        vpath = root / "outputs" / "validation.json"
        report2 = json.loads(vpath.read_text(encoding="utf-8"))
        if errors2 or report2.get("clash_count", 1) != 0:
            print("Presubmit failed (after Option B):\n" + "\n".join(f" - {e}" for e in (errors2 or errors)))
            if report2.get("clash_count", 1) != 0:
                print(f" - clash_count != 0: {report2.get('clash_count')}")
            return 1
        print("Presubmit OK after Option B (relaxed elective minima): no blanks and clash-free.")
        return 0
    print("Presubmit OK: no blanks and clash-free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
