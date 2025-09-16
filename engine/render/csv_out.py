from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..models.timetable import Timetable


def csv_blocks(tt: Timetable, grades: List[str], days: List[str], time_slots: List[dict]) -> str:
    # Header per block: Grade,Day,PeriodStart,PeriodEnd,Subject,Teacher
    lines: List[str] = []
    header = "Grade,Day,PeriodStart,PeriodEnd,Subject,Teacher"
    slots_order = [s["id"] for s in time_slots]
    slots_by_id = {s["id"]: s for s in time_slots}
    for g in grades:
        lines.append(header)
        for d in days:
            for sid in slots_order:
                s = slots_by_id[sid]
                a = tt.get(g, d, sid)
                if s["type"] == "break":
                    lines.append(f"{g},{d},{s['start']},{s['end']},Break,")
                    continue
                if s["type"] == "lunch":
                    lines.append(f"{g},{d},{s['start']},{s['end']},Lunch,")
                    continue
                if a is None:
                    # Leave empty if not placed
                    lines.append(f"{g},{d},{s['start']},{s['end']},,")
                else:
                    teacher = a.teacher or ""
                    lines.append(f"{g},{d},{s['start']},{s['end']},{a.subject},{teacher}")
        lines.append("")  # blank line
    return "\n".join(lines)


def write_csv_blocks(text: str, outputs_dir: Path) -> None:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    with (outputs_dir / "timetable.csv").open("w", encoding="utf-8") as f:
        f.write(text)
