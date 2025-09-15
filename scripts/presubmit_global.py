from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Set, Tuple


def load_exceptions(path: Path) -> Set[str]:
    names: Set[str] = set()
    try:
        import tomllib

        with path.open("rb") as f:
            t = tomllib.load(f)
        names = set((t.get("cross_segment_teachers", {}) or {}).get("names", []) or [])
    except Exception:
        pass
    return names


def read_rows(p: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with p.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(
                {
                    k: row.get(k, "").strip()
                    for k in ["Grade", "Day", "PeriodStart", "PeriodEnd", "Subject", "Teacher"]
                }
            )
    return rows


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Global presubmit for cross-segment guardrail")
    ap.add_argument(
        "--segments-root", type=Path, required=True, help="Path to outputs/runs/<STAMP>"
    )
    ap.add_argument("--exceptions", type=Path, required=True, help="Path to configs/segments.toml")
    args = ap.parse_args(argv)

    exc = load_exceptions(args.exceptions)
    if not exc:
        print("GLOBAL_OK (no exception teachers configured)")
        return 0
    # Enumerate per-segment schedule.csv files
    seg_dirs = [d for d in args.segments_root.iterdir() if d.is_dir() and d.name != "merged"]
    seg_schedules = [d / "schedule.csv" for d in seg_dirs if (d / "schedule.csv").exists()]
    if not seg_schedules:
        print("GLOBAL_OK (no per-segment schedules found)")
        return 0

    # Build teacher->(day,start)->set(segments,grades)
    conflicts: List[str] = []
    by_teacher: Dict[str, Dict[Tuple[str, str], Dict[str, Set[str]]]] = {}
    for d in seg_dirs:
        p = d / "schedule.csv"
        if not p.exists():
            continue
        rows = read_rows(p)
        seg = d.name
        for r in rows:
            t = r["Teacher"]
            if not t or t not in exc:
                continue
            key = (r["Day"], r["PeriodStart"])  # day, start time
            by_teacher.setdefault(t, {}).setdefault(key, {}).setdefault(seg, set()).add(r["Grade"])

    for teacher, slot_map in by_teacher.items():
        for (day, start), seg_map in slot_map.items():
            if len(seg_map) > 1:
                # Collect grades across segments
                grades = []
                for seg, gs in seg_map.items():
                    grades.extend(sorted(gs))
                conflicts.append(
                    f"GLOBAL: XSEG_TEACHER_CONFLICT:{teacher}:{day}:{start}:{'|'.join(grades)}"
                )

    if conflicts:
        print("\n".join(conflicts))
        return 1
    print("GLOBAL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
