from __future__ import annotations

import argparse
import csv
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


def _time_maps(structure: dict) -> tuple[List[str], Dict[str, dict]]:
    ts = [t for t in structure.get("time_slots", []) if t.get("type") == "teaching"]
    by_id = {t["id"]: t for t in structure.get("time_slots", [])}
    return [t["id"] for t in ts], by_id


def _read_schedule_csv(p: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not p.exists():
        return rows
    with p.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
    return rows


def _candidate_teachers(teachers: dict, subject: str, grade: str) -> List[str]:
    out: List[str] = []
    for t in teachers.get("teachers", []):
        subs = set(t.get("subjects", []) or [])
        if subject not in subs:
            continue
        allowed = set(t.get("grades", []) or [])
        gb = _grade_base(grade)
        if grade in allowed or gb in allowed:
            out.append(t.get("name") or t.get("id") or "")
    return sorted([x for x in out if x])


def _load_exceptions(root: Path) -> List[str]:
    try:
        import tomllib

        with (root / "configs" / "segments.toml").open("rb") as f:
            t = tomllib.load(f)
        return list((t.get("cross_segment_teachers", {}) or {}).get("names", []) or [])
    except Exception:
        return []


def explain(grade: str, subject: str, segment: str, out_dir: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    structure, subjects, teachers, constraints = _load_all(root)
    days: List[str] = structure.get("days", [])
    teach_ids, ts_by_id = _time_maps(structure)
    # Latest schedules
    seg_dir = root / "outputs" / "runs" / "latest" / segment
    sched_rows = _read_schedule_csv(seg_dir / "schedule.csv")
    merged_rows = _read_schedule_csv(
        root / "outputs" / "runs" / "latest" / "merged" / "schedule.csv"
    )
    # Candidate teachers
    candidates = _candidate_teachers(teachers, subject, grade)
    # Teacher loads
    load: Dict[str, int] = {name: 0 for name in candidates}
    for r in sched_rows:
        t = r.get("Teacher") or ""
        if t in load:
            load[t] += 1
    # Build occupancy maps
    class_occ = {
        (r.get("Grade"), r.get("Day"), r.get("PeriodStart")): r
        for r in sched_rows
        if (r.get("Subject") or "")
    }
    teacher_occ = {
        (r.get("Teacher"), r.get("Day"), r.get("PeriodStart")): r
        for r in sched_rows
        if (r.get("Teacher") or "")
    }
    # Cross-segment exceptions
    exceptions = set(_load_exceptions(root))
    other_rows = [r for r in merged_rows if Path(seg_dir).name not in (r.get("Grade") or "")]
    other_occ = {
        (r.get("Teacher"), r.get("Day"), r.get("PeriodStart")): r
        for r in other_rows
        if (r.get("Teacher") or "")
    }

    # Reasons
    per_slot: Dict[str, Dict[str, str]] = {}
    for d in days:
        for tid in teach_ids:
            ts = ts_by_id.get(tid, {})
            if ts.get("type") != "teaching":
                continue
            start = ts.get("start")
            key = f"{d}|{tid}"
            per_slot[key] = {}
            # Class blank? If already assigned to another subject, mark as blocked
            if (grade, d, start) in class_occ:
                for name in candidates:
                    per_slot[key][name] = "CLASS_OCCUPIED"
                continue
            for name in candidates:
                # Teacher occupied in-segment
                if (name, d, start) in teacher_occ:
                    per_slot[key][name] = "TEACHER_OCCUPIED"
                    continue
                # Cross-segment occupied (exceptions only)
                if name in exceptions and (name, d, start) in other_occ:
                    per_slot[key][name] = "XSEG_OCCUPIED"
                    continue
                # TODO: teacher day window from overrides
                per_slot[key][name] = "OK"

    # Outputs
    out_dir.mkdir(parents=True, exist_ok=True)
    # coverage.json
    cov = {"grade": grade, "subject": subject, "candidates": candidates}
    (out_dir / "coverage.json").write_text(json.dumps(cov, indent=2), encoding="utf-8")
    # teacher_load.csv
    with (out_dir / "teacher_load.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Teacher", "Used"])
        for name in candidates:
            w.writerow([name, load.get(name, 0)])
    # per_slot.json
    (out_dir / "per_slot.json").write_text(json.dumps(per_slot, indent=2), encoding="utf-8")
    # summary.md
    lines = [
        f"# Explain-Why: {grade} {subject}",
        f"Candidates ({len(candidates)}): {', '.join(candidates)}",
        "",
        "Top tips:",
        "- Consider teacher availability clashes shown as TEACHER_OCCUPIED/XSEG_OCCUPIED.",
        "- Fill class blanks where OK; windows/pins are enforced in solver.",
    ]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Explain-Why diagnostics (visibility only)")
    ap.add_argument("--grade", required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--segment", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    explain(args.grade, args.subject, args.segment, Path(args.out))
    print(json.dumps({"status": "ok", "out": args.out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
