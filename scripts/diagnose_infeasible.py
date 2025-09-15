from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _grade_base(g: str) -> str:
    for i, ch in enumerate(g):
        if ch.isalpha() and i > 0 and g[i - 1].isdigit():
            return g[:i]
    return g


def _load_all(root: Path) -> tuple[dict, dict, dict, dict]:
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from engine.data.loader import load_data  # type: ignore

    data = load_data(root)
    return data.structure, data.subjects, data.teachers, data.constraints


def _teacher_can_teach(t: dict, subject: str, grade: str) -> bool:
    subs = set(t.get("subjects", []))
    if subject not in subs:
        return False
    allowed = set(t.get("grades", []))
    gb = _grade_base(grade)
    return (grade in allowed) or (gb in allowed)


def _time_slots(structure: dict) -> tuple[List[str], Dict[str, dict]]:
    ts = list(structure.get("time_slots", []))
    ids = [t["id"] for t in ts if t.get("type") == "teaching"]
    by_id = {t["id"]: t for t in ts}
    return ids, by_id


def _pe_band_for(g: str, pe_bands: Dict[str, str]) -> str | None:
    gb = _grade_base(g)
    return pe_bands.get(gb)


def diagnose_primary(root: Path, segment: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    structure, subjects, teachers, constraints = _load_all(root)
    grades_all: List[str] = structure.get("grades", [])
    grades = [g for g in grades_all if _grade_base(g) in {"B1", "B2", "B3", "B4", "B5"}]
    days: List[str] = structure.get("days", [])
    teach_ids, ts_by_id = _time_slots(structure)
    weekly = constraints.get("weekly_quotas", {})
    time_windows = constraints.get("time_windows", [])
    # Build allowed-day windows per subject
    subj_windows: Dict[str, set[str]] = {}
    for w in time_windows:
        if w.get("hard"):
            subj = w.get("subject")
            grs = set(w.get("grades", []) or [])
            if any(_grade_base(x) in {"B1", "B2", "B3", "B4", "B5"} for x in grs):
                subj_windows.setdefault(subj, set()).update(set(w.get("days", []) or []))
    # Pins: Extra Curricular at T9 (from structure), PE bands from configs/segments.toml
    pe_bands: Dict[str, str] = {}
    try:
        import tomllib

        with (root / "configs" / "segments.toml").open("rb") as f:
            t = tomllib.load(f)
        pe_bands = {k: str(v) for k, v in (t.get("pe_bands", {}) or {}).items()}
    except Exception:
        pe_bands = {}

    tlist = list(teachers.get("teachers", []))
    shortfalls: List[Dict[str, Any]] = []
    table: Dict[str, Dict[str, Any]] = {}

    for g in grades:
        table[g] = {}
        for s in subjects.get("canonical", []):
            req = int(weekly.get(s, 0))
            if req <= 0:
                continue
            # Per-day capacity: count candidate teaching slots not blocked by fixed pins and with any teacher available
            per_day_cap: Dict[str, int] = {}
            total_cap = 0
            allowed_days = subj_windows.get(s, set()) or set(days)
            for d in days:
                if d not in allowed_days:
                    per_day_cap[d] = 0
                    continue
                cap_d = 0
                for sid in teach_ids:
                    ts = ts_by_id.get(sid, {})
                    if ts.get("type") != "teaching":
                        continue
                    # Block EC at T9
                    if ts.get("fixed_subject"):
                        # Always block fixed, except English special-case for B9 not applicable here
                        cap_flag = False
                    else:
                        # Block PE band slot on Friday according to policy
                        if s != "P.E." and d == "Friday":
                            band = _pe_band_for(g, pe_bands)
                            if band:
                                band_sid = {"P1": "T1", "P2": "T2", "P3": "T3"}.get(str(band))
                                if sid == band_sid:
                                    cap_flag = False
                                else:
                                    cap_flag = True
                            else:
                                cap_flag = True
                        else:
                            cap_flag = True
                    if not cap_flag:
                        continue
                    # Any teacher can teach?
                    ok = any(_teacher_can_teach(t, s, g) for t in tlist)
                    if ok:
                        cap_d += 1
                per_day_cap[d] = cap_d
                total_cap += cap_d
            table[g][s] = {"required": req, "per_day": per_day_cap, "total_capacity": total_cap}
            if total_cap < req:
                # Suggest fix
                fix = "add coverage"
                if s not in {"English", "Mathematics", "Science"}:
                    fix = "widen non-core quota"
                pins = []
                # List blocking pins touching this subject capacity (EC T9, PE bands)
                try:
                    if pe_bands:
                        pins.append("PE band Friday")
                    pins.append("EC T9")
                except Exception:
                    pass
                shortfalls.append(
                    {
                        "grade": g,
                        "subject": s,
                        "required": req,
                        "total_capacity": total_cap,
                        "suggest": fix,
                        "pins": pins,
                    }
                )

    return table, shortfalls


def write_reports(
    root: Path, segment: str, table: Dict[str, Any], shortfalls: List[Dict[str, Any]]
) -> None:
    out_dir = root / "outputs" / "diagnostics" / segment
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps({"table": table, "shortfalls": shortfalls}, indent=2), encoding="utf-8"
    )
    # Markdown
    lines = ["# Feasibility Diagnostics", "", f"Segment: {segment}", ""]
    if shortfalls:
        lines.append("## SHORTFALLS")
        for sf in shortfalls[:10]:
            lines.append(
                f"- {sf['grade']} {sf['subject']}: required={sf['required']} capacity={sf['total_capacity']} | suggest: {sf['suggest']} | pins: {', '.join(sf.get('pins', []))}"
            )
    else:
        lines.append("No shortfalls detected.")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Pre-solve feasibility scan for Primary segment")
    ap.add_argument("--segment", type=str, default="P_B1_B5")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    table, shortfalls = diagnose_primary(root, args.segment)
    write_reports(root, args.segment, table, shortfalls)
    if shortfalls:
        # Print top-10 to stdout for convenience
        for sf in shortfalls[:10]:
            print(
                f"SHORTFALL: {sf['grade']} {sf['subject']}: required={sf['required']} capacity={sf['total_capacity']} | suggest={sf['suggest']} | pins={','.join(sf.get('pins', []))}"
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
