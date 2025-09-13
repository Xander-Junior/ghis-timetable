from __future__ import annotations

from collections import defaultdict, Counter
from typing import Dict, List, Tuple

from ..models.timetable import Timetable


def validate_all(
    tt: Timetable,
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
    weekly_quotas: Dict[str, int],
) -> Dict[str, object]:
    report: Dict[str, object] = {}
    # Collisions
    teacher_slots: Counter = Counter()
    class_slots: Counter = Counter()
    for a in tt.all():
        if a.teacher:
            teacher_slots[(a.teacher, a.day, a.slot_id)] += 1
        class_slots[(a.grade, a.day, a.slot_id)] += 1
    clashes = sum(1 for _, c in teacher_slots.items() if c > 1) + sum(
        1 for _, c in class_slots.items() if c > 1
    )
    report["clash_count"] = clashes

    # Windows: Twi B7–B9 on Wed/Fri; B9 English on Wed/Fri only
    violations_by_rule: Dict[str, List[str]] = defaultdict(list)
    for a in tt.all():
        if a.subject == "Twi" and (a.grade.startswith("B7") or a.grade.startswith("B8") or a.grade.startswith("B9")):
            if a.day not in {"Wednesday", "Friday"}:
                violations_by_rule["twi_window"].append(f"{a.grade} {a.day} {a.slot_id}")
        if a.subject == "English" and a.grade.startswith("B9"):
            if a.day not in {"Wednesday", "Friday"}:
                violations_by_rule["b9_english_days"].append(f"{a.grade} {a.day} {a.slot_id}")

    # Anti-repeat per day
    for g in grades:
        for d in days:
            seen: set[str] = set()
            for a in [x for x in tt.all() if x.grade == g and x.day == d and x.subject not in {"Break", "Lunch", "Extra Curricular"}]:
                if a.subject in seen and not (a.grade.startswith("B9") and a.subject == "English" and a.day in {"Wednesday", "Friday"}):
                    violations_by_rule["repeat_in_day"].append(f"{g} {d} {a.subject}")
                seen.add(a.subject)

    # Quota unmet
    unmet_loads: Dict[Tuple[str, str], int] = {}
    for g in grades:
        placed = Counter(
            a.subject
            for a in tt.all()
            if a.grade == g and a.subject not in {"Break", "Lunch", "Extra Curricular", "UCMAS", "P.E."}
        )
        for subj, q in weekly_quotas.items():
            if subj in {"UCMAS_B1_B8", "UCMAS_B9", "P.E.", "Career Tech/Pre-tech", "OWOP"}:
                # Applicability handled elsewhere; skip strict check here
                continue
            if placed.get(subj, 0) < q:
                unmet_loads[(g, subj)] = q - placed.get(subj, 0)
    report["violations_by_rule"] = dict(violations_by_rule)
    report["unmet_weekly_loads"] = {f"{g}:{s}": v for (g, s), v in unmet_loads.items()}

    # Repetition scan per grade/day
    repetition_scan: Dict[str, Dict[str, List[str]]] = {}
    for g in grades:
        repetition_scan[g] = {}
        for d in days:
            repetition_scan[g][d] = [a.subject for a in sorted(tt.all(), key=lambda x: x.slot_id) if a.grade == g and a.day == d]
    report["repetition_scan"] = repetition_scan

    # Subject concurrency stats: number of parallel same subjects per day/slot
    conc_stats: Dict[str, int] = Counter()
    for d in days:
        for s in [t["id"] for t in time_slots if t["type"] == "teaching"]:
            subj_counts = Counter(a.subject for a in tt.all() if a.day == d and a.slot_id == s)
            for subj, c in subj_counts.items():
                if subj not in {"Break", "Lunch", "Extra Curricular"} and c > 1:
                    conc_stats[f"{d}:{s}:{subj}"] = c
    report["subject_concurrency_stats"] = dict(conc_stats)

    # UCMAS policy check: not same slot across classes and min gap >=1
    ucmas_day_slots: Dict[str, List[str]] = defaultdict(list)
    for a in tt.all():
        if a.subject == "UCMAS":
            ucmas_day_slots[a.day].append(a.slot_id)
    for day, slots in ucmas_day_slots.items():
        # same slot violation if duplicates
        if len(slots) != len(set(slots)):
            violations_by_rule.setdefault("ucmas_same_slot", []).append(day)
        # sort slots by id order T1..T9 and check gaps
        order = {f"T{i}": i for i in range(1, 10)}
        idxs = sorted(order.get(s, 0) for s in slots)
        for i in range(1, len(idxs)):
            if idxs[i] - idxs[i - 1] < 2:
                violations_by_rule.setdefault("ucmas_gap", []).append(f"{day}:{idxs[i-1]}-{idxs[i]}")

    # Cross-grade min gap (>=1 period) for same subject on same day
    for d in days:
        for subj in set(a.subject for a in tt.all() if a.day == d and a.subject not in {"Break", "Lunch", "Extra Curricular"}):
            # map grade -> slot index
            order = {f"T{i}": i for i in range(1, 10)}
            slots = [order.get(a.slot_id, 0) for a in tt.all() if a.day == d and a.subject == subj]
            slots.sort()
            for i in range(1, len(slots)):
                if slots[i] - slots[i - 1] < 2:
                    violations_by_rule.setdefault("cross_grade_min_gap", []).append(f"{d}:{subj}")

    # No hard global uniqueness; concurrency stats above reflect last-resort parallels

    return report
