from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..models.timetable import Timetable


CAT_COLORS = {
    "Core": "#e6f7ff",
    "Language": "#f0e6ff",
    "Humanities": "#fff4e6",
    "Tech": "#e6f2ff",
    "Creative": "#ffe6f0",
    "Life": "#fff7e6",
    "Physical": "#e6ffe6",
    "Program": "#fffbe6",
}

SUBJECT_TEXT = {
    "French": "#0b4f6c",
    "Twi": "#117733",
}


def category_for(subject: str, categories: Dict[str, List[str]]) -> str | None:
    for cat, subs in categories.items():
        if subject in subs:
            return cat
    return None


def build_html(tt: Timetable, structure: dict, constraints: dict) -> str:
    days: List[str] = structure["days"]
    time_slots: List[dict] = structure["time_slots"]
    grades: List[str] = structure["grades"]
    cats: Dict[str, List[str]] = constraints.get("categories", {})

    slots_order = [s["id"] for s in time_slots]
    slots_by_id = {s["id"]: s for s in time_slots}

    def cell_html(grade: str, day: str, sid: str) -> str:
        sdef = slots_by_id[sid]
        a = tt.get(grade, day, sid)
        if sdef["type"] == "break":
            return f"<td class='break'><div class='vcenter'><strong>BREAK</strong></div></td>"
        if sdef["type"] == "lunch":
            return f"<td class='lunch'><div class='vcenter'><strong>LUNCH</strong></div></td>"
        if a is None:
            return "<td class='empty'></td>"
        subj = a.subject
        teacher = a.teacher or ""
        cat = category_for(subj, cats) or ""
        bg = CAT_COLORS.get(cat, "#fafafa")
        color = SUBJECT_TEXT.get(subj, "#111")
        return (
            f"<td style=\"background:{bg}\">"
            f"<div class='cell'><span class='subj' style='color:{color}'>{subj}</span><br/>"
            f"<span class='teacher'>{teacher}</span></div>"
            f"</td>"
        )

    # Legend
    legend_items = "".join(
        f"<div class='legend-item'><span class='swatch' style='background:{clr}'></span>{cat}</div>"
        for cat, clr in CAT_COLORS.items()
    )

    # Per-grade notes
    def notes_for(grade: str) -> str:
        # counts per subject and distinct days
        placed = [a for a in tt.all() if a.grade == grade and (a.subject not in {"Break", "Lunch"})]
        by_subj: Dict[str, List[str]] = {}
        for a in placed:
            by_subj.setdefault(a.subject, []).append(a.day)
        subj_notes = []
        for subj, ds in sorted(by_subj.items()):
            days_set = sorted(set(ds), key=days.index)
            subj_notes.append(f"<li><strong>{subj}</strong>: {len(ds)} placements across days {', '.join(days_set)}</li>")
        # Language category days used (illustrative)
        lang_days = sorted({a.day for a in placed if category_for(a.subject, cats) == "Language"}, key=days.index)
        lang_note = f"<li>Language days used: {len(lang_days)} ({', '.join(lang_days) if lang_days else 'None'})</li>"
        return "<ul>" + lang_note + "".join(subj_notes) + "</ul>"

    # Global justification (no clashes + concurrency stats)
    def global_justification() -> str:
        # teacher/class collisions checked already; derive same-subject-same-time
        conc = []
        for d in days:
            for sid in slots_order:
                subs = [a.subject for a in tt.all() if a.day == d and a.slot_id == sid and a.subject not in {"Break", "Lunch"}]
                seen: Dict[str, int] = {}
                for s in subs:
                    seen[s] = seen.get(s, 0) + 1
                for s, c in seen.items():
                    if c > 1:
                        conc.append((d, sid, s, c))
        if not conc:
            return "<p>No same-subject concurrency across classes; global uniqueness holds at each (day, period).</p>"
        items = "".join(f"<li>{d} {sid}: {s} ×{c}</li>" for d, sid, s, c in conc[:50])
        return (
            "<p>Detected same-subject concurrency across classes at some (day, period) cells (allowed with penalties)." \
            " These are last-resort outcomes and do not cause teacher/class clashes.</p>" \
            f"<ul>{items}</ul>"
        )

    # Build HTML per grade
    blocks = []
    for g in grades:
        head_cells = "".join(
            f"<th>{sid}<br/><span class='time'>{slots_by_id[sid]['start']}–{slots_by_id[sid]['end']}</span></th>"
            for sid in slots_order
        )
        rows_html = []
        for d in days:
            row_cells = "".join(cell_html(g, d, sid) for sid in slots_order)
            rows_html.append(f"<tr><th class='day'>{d}</th>{row_cells}</tr>")
        block = (
            f"<section class='grade'>"
            f"<h2>{g}</h2>"
            f"<table class='tt'>"
            f"<thead><tr><th class='corner'></th>{head_cells}</tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody>"
            f"</table>"
            f"<div class='notes'><h3>Notes & Justification</h3>{notes_for(g)}</div>"
            f"</section>"
        )
        blocks.append(block)

    style = f"""
    <style>
    body {{ font-family: system-ui, Arial, sans-serif; margin: 20px; color: #222; }}
    h1 {{ margin-bottom: 4px; }}
    .legend {{ display:flex; gap:12px; flex-wrap:wrap; margin: 8px 0 20px; }}
    .legend-item {{ display:flex; align-items:center; gap:6px; font-size: 13px; }}
    .legend .swatch {{ width:16px; height:16px; display:inline-block; border:1px solid #ccc; }}
    .grade {{ margin-bottom: 36px; page-break-inside: avoid; }}
    .tt {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
    .tt th, .tt td {{ border: 1px solid #ddd; padding: 6px; vertical-align: middle; text-align: center; }}
    .tt thead th {{ background:#f7f7f7; font-weight:600; }}
    .tt .day {{ background:#fafafa; width: 110px; text-align:left; padding-left:8px; }}
    .tt .corner {{ background:#fff; width:110px; }}
    .time {{ font-size: 11px; color:#666; }}
    .cell {{ line-height: 1.2; }}
    .subj {{ font-weight: 600; }}
    .teacher {{ font-size: 12px; color:#444; }}
    .break, .lunch {{ background:#000; color:#fff; }}
    .break .vcenter, .lunch .vcenter {{ writing-mode: vertical-rl; transform: rotate(180deg); font-weight: 800; font-size: 14px; letter-spacing: 2px; }}
    .empty {{ background:#fbfbfb; }}
    .notes {{ margin-top: 8px; font-size: 13px; color:#333; }}
    .global {{ margin-top: 16px; padding-top: 8px; border-top: 1px dashed #ccc; font-size: 13px; }}
    </style>
    """

    html = (
        "<html><head><meta charset='utf-8'><title>GHIS Timetables</title>" + style + "</head><body>"
        "<h1>GHIS Timetables</h1>"
        f"<div class='legend'><strong>Legend:</strong> {legend_items}</div>"
        + "".join(blocks)
        + f"<div class='global'><h3>Global Justification</h3>{global_justification()}</div>"
        + "</body></html>"
    )
    return html


def write_html_ui(tt: Timetable, structure: dict, constraints: dict, outputs_dir: Path) -> Path:
    ui_dir = outputs_dir / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    html = build_html(tt, structure, constraints)
    out_path = ui_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path

