from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def write_validation_report(report: Dict[str, object], outputs_dir: Path) -> None:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    with (outputs_dir / "validation.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def format_validation_report(report: Dict[str, object]) -> str:
    lines: list[str] = []
    lines.append(f"clash_count: {report.get('clash_count')}")
    violations = report.get("violations_by_rule", {})
    lines.append("violations_by_rule:")
    if isinstance(violations, dict):
        for k, v in violations.items():
            lines.append(f"  - {k}: {len(v)}")
    unmet = report.get("unmet_weekly_loads", {})
    lines.append(f"unmet_weekly_loads: {len(unmet)} entries")
    lines.append("subject_concurrency_stats:")
    conc = report.get("subject_concurrency_stats", {})
    if isinstance(conc, dict):
        for k, v in conc.items():
            lines.append(f"  - {k}: {v}")
    return "\n".join(lines)

