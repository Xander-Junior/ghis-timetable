from __future__ import annotations

import argparse
import csv
import json
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
    with (root / "data" / "structure.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def _collect_tab_meta(dirp: Path) -> dict:
    meta = {"presubmit": None, "metrics": {}, "banner": "", "title": dirp.name}
    m = dirp / "metrics.json"
    p = dirp / "presubmit.txt"
    if m.exists():
        try:
            meta["metrics"] = json.loads(m.read_text(encoding="utf-8"))
        except Exception:
            pass
    if p.exists():
        try:
            meta["presubmit"] = p.read_text(encoding="utf-8").splitlines()[:2]
            if meta["presubmit"] and meta["presubmit"][0].startswith("Presubmit OK"):
                meta["banner"] = "STRICT"
            else:
                meta["banner"] = "DRAFT"
        except Exception:
            pass
    return meta


def _render_grid(
    rows: List[dict],
    structure: dict,
    *,
    pins: dict | None = None,
    explain: dict | None = None,
    hl_grade: str | None = None,
    hl_subject: str | None = None,
) -> str:
    grades = structure["grades"]
    days = structure["days"]
    time_slots = structure["time_slots"]
    slot_by_time: Dict[Tuple[str, str], str] = {
        (ts["start"], ts["end"]): ts["id"] for ts in time_slots
    }
    # Index grid
    grid: Dict[str, Dict[str, Dict[str, Tuple[str, str]]]] = {
        g: {d: {} for d in days} for g in grades
    }
    for r in rows:
        sid = slot_by_time.get((r["PeriodStart"], r["PeriodEnd"]))
        if not sid:
            continue
        grid.setdefault(r["Grade"], {}).setdefault(r["Day"], {})[sid] = (
            (r["Subject"], r["Teacher"]) if r else ("", "")
        )

    styles = """
    <style>
    body { font-family: Inter, Arial, sans-serif; }
    .tabs { margin-bottom: 10px; }
    .tab { display: inline-block; margin-right: 10px; }
    .grade { margin: 20px 0; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ddd; padding: 6px; font-size: 12px; vertical-align: top; }
    th { background: #f5f5f5; }
    .break { background: #fafafa; color: #777; }
    .lunch { background: #f0f9ff; color: #246; }
    .teach { background: #fff; position: relative; }
    .pinned { outline: 2px solid #2196f3; background: #e8f2ff; }
    .teacherless { background: #fff8e1; }
    .subject { font-weight: 600; }
    .teacher { color: #555; font-size: 11px; }
    .dot { position: absolute; right: 4px; bottom: 4px; width: 8px; height: 8px; border-radius: 50%; background: #ff9800; }
    .tooltip { display:none; position:absolute; right:12px; bottom:12px; background:#333; color:#fff; padding:6px; border-radius:4px; font-size:11px; max-width:260px; }
    td:hover .tooltip { display:block; }
    .banner { padding:8px; border: 1px solid #ddd; background:#f9f9f9; margin-bottom:8px; }
    </style>
    """

    def slot_label(ts: dict) -> str:
        return f"{ts['id']}<br><small>{ts['start']}–{ts['end']}</small>"

    parts: List[str] = [styles]
    for g in grades:
        parts.append(f"<div class='grade'><h3>{g}</h3>")
        parts.append("<table>")
        parts.append(
            "<tr><th>Day</th>"
            + "".join(f"<th>{slot_label(ts)}</th>" for ts in time_slots)
            + "</tr>"
        )
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
                content = f"<div class='subject'>{subj or '&nbsp;'}</div>" + (
                    f"<div class='teacher'>{teacher}</div>" if teacher else ""
                )
                classes = ["teach"]
                if subj == "OpenRevision" and not (teacher or "").strip():
                    classes.append("teacherless")
                is_pinned = False
                if pins and subj:
                    try:
                        is_pinned = subj in (pins.get(g, {}).get(d, {}).get(sid, []) or [])
                    except Exception:
                        is_pinned = False
                if is_pinned:
                    classes.append("pinned")
                # Explain overlay on blanks for a focused grade+subject
                tip_html = ""
                dot = ""
                if explain and hl_grade == g and (subj or "") == "":
                    key = f"{d}|{sid}"
                    expl = explain.get("per_slot", {}).get(key, {})
                    if expl:
                        lines = [f"{name}: {msg}" for name, msg in sorted(expl.items())]
                        tip_html = "<div class='tooltip'>" + "<br>".join(lines) + "</div>"
                        dot = "<span class='dot'></span>"
                row_cells.append(f"<td class='{' '.join(classes)}'>{content}{dot}{tip_html}</td>")
            parts.append("<tr>" + "".join(row_cells) + "</tr>")
        parts.append("</table></div>")
    return "\n".join(parts)


def render_schedule_mode(
    root: Path,
    schedule_in: Path,
    out_html: Path,
    title: str,
    pins: dict | None,
    explain_dir: Path | None,
    highlight_grade: str | None,
    highlight_subject: str | None,
    banner: str | None,
) -> None:
    structure = load_structure(root)
    # Determine tabs
    tabs: List[Tuple[str, Path]] = []
    if schedule_in.is_dir():
        # Accept nested: include direct child dirs with schedule.csv
        for d in sorted([p for p in schedule_in.iterdir() if p.is_dir()]):
            if (d / "schedule.csv").exists():
                tabs.append((d.name, d))
        # Also if schedule.csv directly in schedule_in
        if (schedule_in / "schedule.csv").exists():
            tabs.append((schedule_in.name, schedule_in))
    else:
        tabs.append((schedule_in.stem, schedule_in.parent))

    # Optional explain payload
    explain: dict | None = None
    if explain_dir and explain_dir.exists():
        try:
            payload = json.loads((explain_dir / "per_slot.json").read_text(encoding="utf-8"))
            explain = {"per_slot": payload}
        except Exception:
            explain = None

    header = [
        "<html><head><meta charset='utf-8'></head><body>",
        f"<h2>{title}</h2>",
    ]
    # Run stamp (if under outputs/runs/<STAMP>)
    try:
        if "runs" in schedule_in.parts:
            idx = schedule_in.parts.index("runs")
            stamp = schedule_in.parts[idx + 1]
            header.append(
                f"<div class='banner'>Run: {stamp} {('['+banner+']') if banner else ''}</div>"
            )
    except Exception:
        pass

    body: List[str] = []
    # Tabs
    if len(tabs) > 1:
        tab_links = " ".join(f"<a class='tab' href='#{name}'>{name}</a>" for name, _ in tabs)
        body.append(f"<div class='tabs'>{tab_links}</div>")
    for name, d in tabs:
        rows = read_schedule_csv(d / "schedule.csv")
        pins_map = pins
        # Auto-load per-tab pins.json if not provided globally
        if pins_map is None:
            try:
                pj = d / "pins.json"
                if pj.exists():
                    pins_map = json.loads(pj.read_text(encoding="utf-8"))
            except Exception:
                pins_map = None
        # Load tab-specific pins if provided per dir later (not in scope)
        meta = _collect_tab_meta(d)
        body.append(f"<h3 id='{name}'>{name} — {meta.get('banner','')}</h3>")
        if meta.get("presubmit"):
            body.append("<pre>" + "\n".join(meta["presubmit"]) + "</pre>")
        grid_html = _render_grid(
            rows,
            structure,
            pins=pins_map,
            explain=explain,
            hl_grade=highlight_grade,
            hl_subject=highlight_subject,
        )
        body.append(grid_html)
    footer = ["</body></html>"]
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text("\n".join(header + body + footer), encoding="utf-8")


def render_diagnostics_mode(root: Path, diag_json: Path, out_html: Path, title: str) -> None:
    # Load diagnostics
    try:
        data = json.loads(diag_json.read_text(encoding="utf-8"))
    except Exception:
        data = {"shortfalls": [], "table": {}}
    shortfalls = data.get("shortfalls", [])
    # Optional JHS schedule tab if present
    jhs_dir = root / "outputs" / "runs" / "latest" / "JHS_B6"
    schedule_tab = None
    if (jhs_dir / "schedule.csv").exists():
        schedule_tab = jhs_dir

    parts: List[str] = ["<html><head><meta charset='utf-8'></head><body>", f"<h2>{title}</h2>"]
    parts.append("<h3>Primary Shortfalls</h3>")
    if not shortfalls:
        parts.append("<p>No shortfalls detected.</p>")
    else:
        parts.append("<ul>")
        for sf in shortfalls:
            pins = ", ".join(sf.get("pins", []))
            parts.append(
                f"<li>{sf.get('grade')} {sf.get('subject')}: required={sf.get('required')} capacity={sf.get('total_capacity')} | suggest: {sf.get('suggest')} | pins: {pins}</li>"
            )
        parts.append("</ul>")
    # Schedule tab if exists
    if schedule_tab:
        parts.append("<hr>")
        parts.append("<h3>JHS Schedule</h3>")
        rows = read_schedule_csv(schedule_tab / "schedule.csv")
        grid_html = _render_grid(rows, load_structure(root))
        parts.append(grid_html)
    parts.append("</body></html>")
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text("\n".join(parts), encoding="utf-8")


def parse_args(argv: List[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="GHIS UI builder")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--schedule", type=str, help="Path to schedule.csv or directory containing segment folders"
    )
    g.add_argument("--diagnostics", type=str, help="Path to diagnostics report.json")
    ap.add_argument("--out", type=str, required=True, help="Output HTML path")
    ap.add_argument("--title", type=str, default="GHIS Timetable – Latest")
    ap.add_argument("--pins", type=str, default=None)
    ap.add_argument("--explain-dir", type=str, default=None)
    ap.add_argument("--highlight-grade", type=str, default=None)
    ap.add_argument("--highlight-subject", type=str, default=None)
    ap.add_argument("--banner", type=str, default=None, choices=["STRICT", "DRAFT"])
    return ap.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv[1:])
    root = Path(__file__).resolve().parents[1]
    out_html = Path(args.out).resolve()
    if args.schedule:
        schedule_in = Path(args.schedule).resolve()
        pins = None
        if args.pins:
            p = Path(args.pins)
            if p.exists():
                try:
                    pins = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pins = None
        explain_dir = Path(args.explain_dir).resolve() if args.explain_dir else None
        render_schedule_mode(
            root,
            schedule_in,
            out_html,
            args.title,
            pins,
            explain_dir,
            args.highlight_grade,
            args.highlight_subject,
            args.banner,
        )
    else:
        diag_json = Path(args.diagnostics).resolve()
        render_diagnostics_mode(root, diag_json, out_html, args.title)
    print(str(out_html))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
