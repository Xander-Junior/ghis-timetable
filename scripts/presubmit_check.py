from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import os
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

    # 2) Teacher conflicts (same teacher in multiple classes at same time)
    teacher_slot: Dict[Tuple[str, Tuple[str, str], str], List[Row]] = {}
    for r in rows:
        if r.is_teaching_cell() and r.teacher:
            teacher_slot.setdefault((r.day, r.slot, r.teacher), []).append(r)
    for _, group in teacher_slot.items():
        if len(group) > 1:
            for r in group:
                add_error(r, "TEACHER_CONFLICT")

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
        if r.subject == "Twi" and (r.grade.startswith("B7") or r.grade.startswith("B8") or r.grade.startswith("B9")):
            if r.day not in {"Wednesday", "Friday"}:
                add_error(r, "TWI_WINDOW_VIOLATION")

    # 5) B9 English on both Wed and Fri
    b9_english_days = {r.day for r in rows if r.grade == "B9" and r.subject == "English"}
    for d in ("Wednesday", "Friday"):
        if d not in b9_english_days:
            global_errors.append(f"B9_ENGLISH_DAY_MISSING:{d}")

    # 6) B9 Friday last period (T9) must be English; Extra Curricular forbidden
    b9_fri_rows = [r for r in rows if r.grade == "B9" and r.day == "Friday"]
    if b9_fri_rows:
        ordered = sorted(b9_fri_rows, key=lambda r: (_time_key(r.start), _time_key(r.end)))
        teaching_rows = [r for r in ordered if r.is_teaching_cell()]
        if teaching_rows:
            last = teaching_rows[-1]
            if last.subject == "Extra Curricular":
                add_error(last, "EC_FORBIDDEN_T9_B9_FRI")
            if last.subject != "English":
                add_error(last, "B9_FRI_T9_ENGLISH_REQUIRED")
        else:
            global_errors.append("B9_FRI_T9_ENGLISH_REQUIRED:NoTeachingRow")
    else:
        global_errors.append("B9_FRI_T9_ENGLISH_REQUIRED:NoFridayRows")

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
            per_grade_slot_subj.setdefault(key, {})[r.subject] = per_grade_slot_subj.setdefault(key, {}).get(r.subject, 0) + 1
    for (g, _), subj_counts in per_grade_slot_subj.items():
        for c in subj_counts.values():
            if c > 1:
                same_slot_repeat_score += c - 1
                same_slot_by_grade[g] = same_slot_by_grade.get(g, 0) + (c - 1)

    fallback_usage = sum(1 for r in rows if r.subject == "Supervised Study")

    # Apply B9 English exception at per-grade level as well (subtract exactly one if present)
    if b9_english_adjacent > 0:
        adjacency_by_grade["B9"] = max(0, adjacency_by_grade.get("B9", 0) - 1)

    metrics = {
        "adjacency_violations": adjacency_violations,
        "same_slot_repeat_score": same_slot_repeat_score,
        "fallback_usage": fallback_usage,
        # Per-grade breakdowns used by strict mode
        "adjacency_by_grade": adjacency_by_grade,
        "same_slot_by_grade": same_slot_by_grade,
    }

    return errors, metrics, global_errors


def _format_failure_output(rows: List[Row], errors: Dict[int, List[str]], global_errors: List[str]) -> str:
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
    parser.add_argument("csv_path", type=Path, help="Path to schedule.csv (Grade,Day,Start,End,Subject,Teacher)")
    parser.add_argument("--strict", action="store_true", help="Enable strict mode thresholds")
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
