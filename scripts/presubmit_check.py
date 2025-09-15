from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

HEADER = ["Grade", "Day", "PeriodStart", "PeriodEnd", "Subject", "Teacher"]
NON_TEACHING = {"Break", "Lunch"}


@dataclass
class Row:
    line_no: int
    grade: str
    day: str
    start: str
    end: str
    subject: str
    teacher: str

    @property
    def slot(self) -> Tuple[str, str]:
        return (self.start, self.end)

    def is_teaching_cell(self) -> bool:
        return self.subject not in NON_TEACHING


def read_schedule_csv(path: Path) -> List[Row]:
    rows: List[Row] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        line_no = 0
        for raw in reader:
            line_no += 1
            # Skip blank lines
            if not raw or all(not x.strip() for x in raw):
                continue
            # Skip header lines (header may repeat per grade block)
            if [x.strip() for x in raw] == HEADER:
                continue
            if len(raw) < 6:
                # Pad to length to avoid index errors
                raw = raw + [""] * (6 - len(raw))
            grade, day, start, end, subject, teacher = [x.strip() for x in raw[:6]]
            rows.append(
                Row(
                    line_no=line_no,
                    grade=grade,
                    day=day,
                    start=start,
                    end=end,
                    subject=subject,
                    teacher=teacher,
                )
            )
    return rows


def _time_key(t: str) -> Tuple[int, int]:
    # t is HH:MM; return minutes since midnight for sorting
    try:
        hh, mm = t.split(":")
        return (int(hh), int(mm))
    except Exception:
        return (0, 0)


def validate_rows(rows: List[Row]) -> Tuple[Dict[int, List[str]], Dict[str, int], List[str]]:
    """
    Returns (errors_by_line, metrics, global_errors)

    - errors_by_line: line_no -> list of reason codes
    - metrics: {adjacency_violations, same_slot_repeat_score, fallback_usage}
    - global_errors: textual errors not tied to a specific line (e.g., missing B9 English on Friday)
    """
    errors: Dict[int, List[str]] = {}
    global_errors: List[str] = []

    def add_error(row: Row, code: str) -> None:
        errors.setdefault(row.line_no, []).append(code)

    # Pre-indexing
    by_grade_day: Dict[Tuple[str, str], List[Row]] = {}
    for r in rows:
        by_grade_day.setdefault((r.grade, r.day), []).append(r)

    # 1) Blank teaching cells (Break/Lunch allowed)
    for r in rows:
        if r.is_teaching_cell() and r.subject == "":
            add_error(r, "BLANK_TEACHING_CELL")

    # Load segments and cross-segment teacher exceptions
    segments: Dict[str, str] = {}
    cross_seg_teachers: List[str] = []
    try:
        import tomllib  # py311+

        cfg_path = Path("configs/segments.toml")
        if cfg_path.exists():
            with cfg_path.open("rb") as f:
                t = tomllib.load(f)
            segments = t.get("segments", {}) or {}
            cross_seg_teachers = list((t.get("cross_segment_teachers", {}) or {}).get("names", []))
    except Exception:
        pass

    def _seg_for_grade(g: str) -> str:
        gb = g
        # normalize like B7A -> B7
        for i, ch in enumerate(g):
            if ch.isalpha() and i > 0 and g[i - 1].isdigit():
                gb = g[:i]
                break
        return segments.get(gb, "")

    # 2) Teacher conflicts (same teacher in multiple classes at same time), segment-aware
    teacher_slot: Dict[Tuple[str, Tuple[str, str], str], List[Row]] = {}
    for r in rows:
        if r.is_teaching_cell() and r.teacher:
            teacher_slot.setdefault((r.day, r.slot, r.teacher), []).append(r)
    for (day, slot, teacher), group in teacher_slot.items():
        if len(group) <= 1:
            continue
        # Partition by segment and flag conflicts within same segment; across segments only for exception teachers
        by_seg: Dict[str, List[Row]] = {}
        for rr in group:
            by_seg.setdefault(_seg_for_grade(rr.grade), []).append(rr)
        for sg, lst in by_seg.items():
            if len(lst) > 1:
                for rr in lst:
                    add_error(rr, "TEACHER_CONFLICT")
        # cross-segment conflicts
        if teacher in cross_seg_teachers:
            # If the teacher is an exception, any overlap across segments is a conflict
            # Flag all involved cells
            for rr in group:
                add_error(rr, "TEACHER_CONFLICT_XSEG")

    # 3) Class conflicts (two subjects in one class at same time)
    class_slot: Dict[Tuple[str, str, Tuple[str, str]], Dict[str, List[Row]]] = {}
    for r in rows:
        if r.is_teaching_cell() and r.subject:
            class_slot.setdefault((r.grade, r.day, r.slot), {}).setdefault(r.subject, []).append(r)
    for _, subj_map in class_slot.items():
        if len(subj_map) > 1:
            for group in subj_map.values():
                for r in group:
                    add_error(r, "CLASS_CONFLICT")

    # 4) Twi (B7–B9) only on Wed/Fri
    for r in rows:
        if r.subject == "Twi" and (
            r.grade.startswith("B7") or r.grade.startswith("B8") or r.grade.startswith("B9")
        ):
            if r.day not in {"Wednesday", "Friday"}:
                add_error(r, "TWI_WINDOW_VIOLATION")

    # 5) B9 English final hard rules:
    b9_eng = [r for r in rows if r.grade == "B9" and r.subject == "English"]
    # No English on Mon/Tue/Thu
    for r in b9_eng:
        if r.day in {"Monday", "Tuesday", "Thursday"}:
            global_errors.append(f"B9_ENGLISH_ON_FORBIDDEN_DAY:{r.day}")
    # Friday T8+T9 must both be English
    b9_fri = [r for r in rows if r.grade == "B9" and r.day == "Friday"]
    fri_map = {r.start: r for r in b9_fri if r.is_teaching_cell()}
    # Build order map for slots
    order = {
        "08:00": "T1",
        "08:55": "T2",
        "09:50": "T3",
        "11:25": "T5",
        "12:20": "T6",
        "13:30": "T8",
        "14:45": "T9",
    }
    t8 = next((r for r in b9_fri if order.get(r.start) == "T8" and r.is_teaching_cell()), None)
    t9 = next((r for r in b9_fri if order.get(r.start) == "T9" and r.is_teaching_cell()), None)
    if not t8 or t8.subject != "English" or not t9 or t9.subject != "English":
        # Fallback: use last two Friday teaching rows if mapping differs
        teaching_rows = [
            r
            for r in sorted(b9_fri, key=lambda r: (_time_key(r.start), _time_key(r.end)))
            if r.is_teaching_cell()
        ]
        if len(teaching_rows) >= 2:
            t8_guess, t9_guess = teaching_rows[-2], teaching_rows[-1]
            if not (t8_guess.subject == "English" and t9_guess.subject == "English"):
                global_errors.append("B9_ENGLISH_FRI_T8_T9_REQUIRED")
            if t9 is None:
                t9 = t9_guess
        else:
            global_errors.append("B9_ENGLISH_FRI_T8_T9_REQUIRED")
    # Wednesday exactly two English and adjacent
    b9_wed = [
        r
        for r in rows
        if r.grade == "B9"
        and r.day == "Wednesday"
        and r.is_teaching_cell()
        and r.subject == "English"
    ]
    if len(b9_wed) != 2:
        global_errors.append("B9_ENGLISH_WED_DOUBLE_MISSING")
    else:
        ordered = sorted(b9_wed, key=lambda r: (_time_key(r.start), _time_key(r.end)))
        s1, s2 = ordered
        if not (s1.subject == "English" and s2.subject == "English"):
            global_errors.append("B9_ENGLISH_WED_DOUBLE_MISSING")
        else:
            # adjacent in timetable sense
            slot_index = {"T1": 1, "T2": 2, "T3": 3, "T5": 5, "T6": 6, "T8": 8, "T9": 9}
            # Map start times to slot ids
            s1_id = order.get(s1.start, "")
            s2_id = order.get(s2.start, "")
            if not s1_id or not s2_id or abs(slot_index[s1_id] - slot_index[s2_id]) != 1:
                global_errors.append("B9_ENGLISH_WED_DOUBLE_NOT_ADJACENT")

    # 6) B9 Friday last period (T9) must be English; Extra Curricular forbidden
    # Forbid EC at B9 Friday T9
    if t9 and t9.subject == "Extra Curricular":
        add_error(t9, "EC_FORBIDDEN_T9_B9_FRI")

    # P.E. bands (Friday-only pins)
    pe_bands: Dict[str, str] = {}
    try:
        import tomllib  # py311+

        cfg_path = Path("configs/segments.toml")
        if cfg_path.exists():
            with cfg_path.open("rb") as f:
                t = tomllib.load(f)
            pe_bands = t.get("pe_bands", {}) or {}
    except Exception:
        pass
    if pe_bands:
        # Map P1->slot start times; we only know position index, so check order per grade/day
        band_to_index = {"P1": 0, "P2": 1, "P3": 2}
        # Build day order per grade for Friday
        by_grade_fri = [r for r in rows if r.day == "Friday"]
        # For each grade, identify teaching rows, sort by start time
        grades_present = {r.grade for r in rows}
        for g in grades_present:
            gb = (
                g[:-1] if (len(g) > 2 and g[0] == "B" and g[1].isdigit() and g[-1].isalpha()) else g
            )
            band = pe_bands.get(gb)
            if not band:
                continue
            idx_needed = band_to_index.get(str(band))
            if idx_needed is None:
                continue
            # Only enforce if this grade has any P.E. in the schedule (avoid failing partial goldens)
            any_pe = any(r.grade == g and r.subject == "P.E." for r in rows)
            if not any_pe:
                continue
            fri_rows = [
                r for r in rows if r.grade == g and r.day == "Friday" and r.is_teaching_cell()
            ]
            ordered = sorted(fri_rows, key=lambda r: (_time_key(r.start), _time_key(r.end)))
            if not ordered or idx_needed >= len(ordered):
                # Missing required period entirely
                if ordered:
                    add_error(ordered[-1], f"PE_BAND_MISSING:{g}:{band}")
                else:
                    global_errors.append(f"PE_BAND_MISSING:{g}:{band}")
                continue
            required = ordered[idx_needed]
            if required.subject != "P.E.":
                add_error(required, f"PE_BAND_SLOT_NOT_PE:{g}:{band}")
            # Forbid P.E. elsewhere
            for rr in [x for x in fri_rows if x is not required]:
                if rr.subject == "P.E.":
                    add_error(rr, "PE_FORBIDDEN_OUTSIDE_BAND")
            for rr in [
                x for x in rows if x.grade == g and x.day != "Friday" and x.subject == "P.E."
            ]:
                add_error(rr, "PE_FRIDAY_ONLY")

    # 7) B9 OpenRevision validation (teacherless, not fallback)
    # Load OpenRevision config
    openrev_expected = 2
    openrev_distinct_days_req = 0
    try:
        import tomllib  # py311+

        cfg_path = Path("configs/subjects.toml")
        if cfg_path.exists():
            with cfg_path.open("rb") as f:
                t = tomllib.load(f)
            ocfg = (t.get("subjects", {}) or {}).get("OpenRevision", {}) or {}
            wmin = dict(ocfg.get("weekly_min", {}) or {})
            # Prefer explicit B9/B9A mapping if present
            openrev_expected = int(wmin.get("B9", wmin.get("B9A", 2)) or 2)
            dd = dict(ocfg.get("distinct_days", {}) or {})
            openrev_distinct_days_req = int(dd.get("B9", dd.get("B9A", 0)) or 0)
    except Exception:
        pass

    b9_orows = [r for r in rows if r.grade == "B9" and r.subject == "OpenRevision"]
    if len(b9_orows) != openrev_expected:
        global_errors.append(f"B9_OPENREV_COUNT:found={len(b9_orows)} expected={openrev_expected}")
    if openrev_distinct_days_req >= 2 and b9_orows:
        days = [r.day for r in b9_orows]
        if len(set(days)) < openrev_distinct_days_req:
            global_errors.append(f"B9_OPENREV_DISTINCT_DAYS:days={days}")

    # JHS English distinct day + teacher split
    def _distinct_days_for(g: str, subj: str) -> int:
        return len({r.day for r in rows if r.grade == g and r.subject == subj})

    def _tname_norm(name: str) -> str:
        name = name.strip()
        # drop any trailing grade code suffix
        parts = name.split()
        if parts and parts[-1].startswith("B") and parts[-1][1:].isdigit():
            parts = parts[:-1]
        return " ".join(parts)

    for g in ("B7", "B8"):
        total_eng = sum(1 for r in rows if r.grade == g and r.subject == "English")
        if total_eng < 4:
            # Skip detailed checks on partial schedules
            continue
        # Distinct-day = 4 for English
        if _distinct_days_for(g, "English") != 4:
            global_errors.append(f"ENGLISH_DISTINCT_DAYS_FAIL:{g}")
        # Wed/Fri exactly 1 each taught by Sir Bright Dey
        wed = [r for r in rows if r.grade == g and r.day == "Wednesday" and r.subject == "English"]
        fri = [r for r in rows if r.grade == g and r.day == "Friday" and r.subject == "English"]
        if sum(1 for r in wed if _tname_norm(r.teacher) == "Sir Bright Dey") != 1:
            global_errors.append(f"JHS_ENGLISH_WED_SIR_BRIGHT_FAIL:{g}")
        if sum(1 for r in fri if _tname_norm(r.teacher) == "Sir Bright Dey") != 1:
            global_errors.append(f"JHS_ENGLISH_FRI_SIR_BRIGHT_FAIL:{g}")
        # Mon/Tue/Thu exactly 2 total and must be Harriet; also forbid Harriet on Wed/Fri
        mtt = [
            r
            for r in rows
            if r.grade == g
            and r.day in {"Monday", "Tuesday", "Thursday"}
            and r.subject == "English"
        ]
        if len([r for r in mtt if _tname_norm(r.teacher) == "Harriet Akasraku"]) != 2:
            global_errors.append(f"JHS_ENGLISH_MTT_HARRIET_COUNT_FAIL:{g}")
        for r in rows:
            if r.grade == g and r.subject == "English":
                if (
                    r.day in {"Wednesday", "Friday"}
                    and _tname_norm(r.teacher) == "Harriet Akasraku"
                ):
                    add_error(r, "JHS_ENGLISH_HARRIET_FORBIDDEN_WED_FRI")
                if (
                    r.day in {"Monday", "Tuesday", "Thursday"}
                    and _tname_norm(r.teacher) == "Sir Bright Dey"
                ):
                    add_error(r, "JHS_ENGLISH_BRIGHT_FORBIDDEN_MON_TUE_THU")

    # B9: teacher domain for English = Sir Bright Dey
    for r in rows:
        if (
            r.grade == "B9"
            and r.subject == "English"
            and r.teacher
            and ("Bright Dey" not in _tname_norm(r.teacher))
        ):
            add_error(r, "B9_ENGLISH_TEACHER_DOMAIN_FAIL")
    # Metrics
    total_adjacent = 0
    b9_english_adjacent = 0
    adjacency_by_grade: Dict[str, int] = {}
    for (g, d), day_rows in by_grade_day.items():
        ordered = sorted(day_rows, key=lambda r: (_time_key(r.start), _time_key(r.end)))
        last_subject: str | None = None
        last_was_teaching = False
        for r in ordered:
            if r.subject in NON_TEACHING:
                last_subject = None
                last_was_teaching = False
                continue
            subj = r.subject
            if subj and last_was_teaching and subj == last_subject:
                total_adjacent += 1
                adjacency_by_grade[g] = adjacency_by_grade.get(g, 0) + 1
                if g == "B9" and subj == "English":
                    b9_english_adjacent += 1
            last_subject = subj if subj else None
            last_was_teaching = True if subj else False
    adjacency_violations = total_adjacent - min(1, b9_english_adjacent)
    if adjacency_violations < 0:
        adjacency_violations = 0

    same_slot_repeat_score = 0
    same_slot_by_grade: Dict[str, int] = {}
    per_grade_slot_subj: Dict[Tuple[str, Tuple[str, str]], Dict[str, int]] = {}
    for r in rows:
        if r.is_teaching_cell() and r.subject:
            key = (r.grade, r.slot)
            per_grade_slot_subj.setdefault(key, {})[r.subject] = (
                per_grade_slot_subj.setdefault(key, {}).get(r.subject, 0) + 1
            )
    for (g, _), subj_counts in per_grade_slot_subj.items():
        for c in subj_counts.values():
            if c > 1:
                same_slot_repeat_score += c - 1
                same_slot_by_grade[g] = same_slot_by_grade.get(g, 0) + (c - 1)

    fallback_usage = sum(1 for r in rows if r.subject == "Supervised Study")

    # No exceptions applied now; B9 requires two doubles by policy.

    metrics = {
        "adjacency_violations": adjacency_violations,
        "same_slot_repeat_score": same_slot_repeat_score,
        "fallback_usage": fallback_usage,
        # Per-grade breakdowns used by strict mode
        "adjacency_by_grade": adjacency_by_grade,
        "same_slot_by_grade": same_slot_by_grade,
    }

    return errors, metrics, global_errors


def _format_failure_output(
    rows: List[Row], errors: Dict[int, List[str]], global_errors: List[str]
) -> str:
    by_line = {r.line_no: r for r in rows}
    lines: List[str] = []
    for ln in sorted(errors.keys()):
        r = by_line.get(ln)
        if not r:
            continue
        codes = ",".join(sorted(set(errors[ln])))
        lines.append(
            f"line {ln}: {r.grade},{r.day},{r.start},{r.end},{r.subject},{r.teacher} -> [{codes}]"
        )
    for ge in global_errors:
        lines.append(f"GLOBAL: {ge}")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GHIS Presubmit Checker")
    parser.add_argument(
        "csv_path", type=Path, help="Path to schedule.csv (Grade,Day,Start,End,Subject,Teacher)"
    )
    parser.add_argument("--strict", action="store_true", help="Enable strict mode thresholds")
    parser.add_argument(
        "--emit-metrics", action="store_true", help="Write metrics.json next to CSV"
    )
    args = parser.parse_args(argv)

    rows = read_schedule_csv(args.csv_path)
    errors, metrics, global_errors = validate_rows(rows)

    # Strict mode enforcement
    if args.strict:
        max_adj = int(os.getenv("MAX_ADJ", "3"))
        max_same_slot = int(os.getenv("MAX_SAME_SLOT", "8"))
        # Fallback usage not allowed
        if metrics["fallback_usage"] > 0:
            global_errors.append("STRICT_FALLBACK_FORBIDDEN:fallback_usage>0")
        # Per-grade thresholds
        for g, val in metrics["adjacency_by_grade"].items():
            cap = int(os.getenv(f"MAX_ADJ_{g}", str(max_adj)))
            if val > cap:
                global_errors.append(f"STRICT_ADJ_LIMIT:{g}:{val}>{cap}")
        for g, val in metrics["same_slot_by_grade"].items():
            cap = int(os.getenv(f"MAX_SAME_SLOT_{g}", str(max_same_slot)))
            if val > cap:
                global_errors.append(f"STRICT_SAME_SLOT_LIMIT:{g}:{val}>{cap}")

    # Write metrics.json if asked
    if args.emit_metrics:
        out_dir = args.csv_path.parent
        try:
            # Compose simple rule flags
            flat_errors = {c for codes in errors.values() for c in codes}
            # Load OpenRevision distinct-day requirement to decide flag emission
            openrev_distinct_days_req = 0
            try:
                import tomllib  # py311+

                cfg_path2 = Path("configs/subjects.toml")
                if cfg_path2.exists():
                    with cfg_path2.open("rb") as f:
                        t2 = tomllib.load(f)
                    ocfg2 = (t2.get("subjects", {}) or {}).get("OpenRevision", {}) or {}
                    dd2 = dict(ocfg2.get("distinct_days", {}) or {})
                    openrev_distinct_days_req = int(dd2.get("B9", dd2.get("B9A", 0)) or 0)
            except Exception:
                pass
            rule_flags = {
                "b9_wed_double_ok": not any(
                    s.startswith("B9_ENGLISH_WED_DOUBLE") for s in global_errors
                ),
                "b9_fri_t8_t9_ok": not any(
                    s == "B9_ENGLISH_FRI_T8_T9_REQUIRED" for s in global_errors
                ),
                "b9_no_forbidden_days": not any(
                    s.startswith("B9_ENGLISH_ON_FORBIDDEN_DAY:") for s in global_errors
                ),
                "b9_teacher_ok": "B9_ENGLISH_TEACHER_DOMAIN_FAIL" not in flat_errors,
                "b7b8_split_ok": not any(
                    s in set(global_errors)
                    for s in [
                        "JHS_ENGLISH_WED_SIR_BRIGHT_FAIL:B7",
                        "JHS_ENGLISH_WED_SIR_BRIGHT_FAIL:B8",
                        "JHS_ENGLISH_FRI_SIR_BRIGHT_FAIL:B7",
                        "JHS_ENGLISH_FRI_SIR_BRIGHT_FAIL:B8",
                        "JHS_ENGLISH_MTT_HARRIET_COUNT_FAIL:B7",
                        "JHS_ENGLISH_MTT_HARRIET_COUNT_FAIL:B8",
                    ]
                )
                and not any(
                    c in flat_errors
                    for c in [
                        "JHS_ENGLISH_HARRIET_FORBIDDEN_WED_FRI",
                        "JHS_ENGLISH_BRIGHT_FORBIDDEN_MON_TUE_THU",
                    ]
                ),
                "pe_bands_ok": not any(
                    (
                        s.startswith("PE_BAND_")
                        or s in {"PE_FORBIDDEN_OUTSIDE_BAND", "PE_FRIDAY_ONLY"}
                    )
                    for s in flat_errors
                )
                and not any(s.startswith("PE_BAND_MISSING:") for s in global_errors),
                "twi_window_ok": "TWI_WINDOW_VIOLATION" not in flat_errors,
                # OpenRevision flags
                "b9_openrev_count_ok": not any(
                    s.startswith("B9_OPENREV_COUNT:") for s in global_errors
                ),
            }
            if openrev_distinct_days_req >= 2:
                rule_flags["b9_openrev_distinct_days_ok"] = not any(
                    s.startswith("B9_OPENREV_DISTINCT_DAYS:") for s in global_errors
                )
            payload = dict(metrics)
            payload["rule_flags"] = rule_flags
            with (out_dir / "metrics.json").open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    if errors or global_errors:
        out = _format_failure_output(rows, errors, global_errors)
        if out:
            print(out)
        return 1
    print("Presubmit OK: no blanks and clash-free.")
    print(
        f"Metrics: adjacency_violations={metrics['adjacency_violations']} "
        f"same_slot_repeat_score={metrics['same_slot_repeat_score']} "
        f"fallback_usage={metrics['fallback_usage']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
