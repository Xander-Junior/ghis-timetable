from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple


HEADER = ["Grade", "Day", "PeriodStart", "PeriodEnd", "Subject", "Teacher"]


def read_schedule_csv(path: Path) -> List[dict]:
    rows: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: r.get(k, "").strip() for k in HEADER})
    return rows


def load_structure(root: Path) -> dict:
    import json

    with (root / "data" / "structure.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def generate_html(rows: List[dict], structure: dict) -> str:
    grades = structure["grades"]
    days = structure["days"]
    time_slots = structure["time_slots"]
    order: Dict[str, int] = {ts["id"]: i for i, ts in enumerate(time_slots, start=1)}
    slot_by_time: Dict[Tuple[str, str], str] = {
        (ts["start"], ts["end"]): ts["id"] for ts in time_slots
    }

    # Index by grade->day->slot
    grid: Dict[str, Dict[str, Dict[str, Tuple[str, str]]]] = {}
    for g in grades:
        grid[g] = {d: {} for d in days}
    for r in rows:
        g, d = r["Grade"], r["Day"]
        sid = slot_by_time.get((r["PeriodStart"], r["PeriodEnd"]))
        if not sid:
            continue
        grid.setdefault(g, {}).setdefault(d, {})[sid] = (r["Subject"], r["Teacher"]) if r else ("", "")

    # Simple HTML table per grade
    styles = """
    <style>
    body { font-family: Inter, Arial, sans-serif; }
    .grade { margin: 20px 0; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 6px; font-size: 12px; }
    th { background: #f5f5f5; }
    .break { background: #fafafa; color: #777; }
    .lunch { background: #f0f9ff; color: #246; }
    .teach { background: #fff; }
    .subject { font-weight: 600; }
    .teacher { color: #555; font-size: 11px; }
    </style>
    """

    def slot_label(ts: dict) -> str:
        return f"{ts['id']}<br><small>{ts['start']}–{ts['end']}</small>"

    parts: List[str] = ["<html><head><meta charset='utf-8'>", styles, "</head><body>"]
    parts.append("<h2>Schedule Grid</h2>")
    for g in grades:
        parts.append(f"<div class='grade'><h3>{g}</h3>")
        parts.append("<table>")
        # Header row
        parts.append("<tr><th>Day</th>" + "".join(f"<th>{slot_label(ts)}</th>" for ts in time_slots) + "</tr>")
        for d in days:
            row_cells: List[str] = [f"<td><b>{d}</b></td>"]
            for ts in time_slots:
                typ = ts.get("type", "teaching")
                if typ == "break":
                    row_cells.append("<td class='break'>Break</td>")
                    continue
                if typ == "lunch":
                    row_cells.append("<td class='lunch'>Lunch</td>")
                    continue
                sid = ts["id"]
                subj, teacher = grid.get(g, {}).get(d, {}).get(sid, ("", ""))
                content = (
                    f"<div class='subject'>{subj or '&nbsp;'}</div>"
                    + (f"<div class='teacher'>{teacher}</div>" if teacher else "")
                )
                row_cells.append(f"<td class='teach'>{content}</td>")
            parts.append("<tr>" + "".join(row_cells) + "</tr>")
        parts.append("</table></div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def main(argv: List[str]) -> int:
    if len(argv) < 3:
        print("Usage: python scripts/build_ui.py <schedule.csv> <out.html>")
        return 2
    schedule = Path(argv[1]).resolve()
    out_html = Path(argv[2]).resolve()
    root = Path(__file__).resolve().parents[1]
    rows = read_schedule_csv(schedule)
    structure = load_structure(root)
    html = generate_html(rows, structure)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    print(str(out_html))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

