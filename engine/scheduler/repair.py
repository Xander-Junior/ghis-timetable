from __future__ import annotations

from typing import Dict, List, Tuple

from ..models.timetable import Timetable
from ..data.registry import OccupancyLedger, SubjectQuotas
from ..models.assignment import Assignment
from ..data.teachers import TeacherDirectory


def repair_schedule(
    tt: Timetable,
    ledger: OccupancyLedger,
    grades: List[str],
    days: List[str],
    teachers: TeacherDirectory | None = None,
    quotas: SubjectQuotas | None = None,
    max_swaps: int = 100,
    time_slots: List[dict] | None = None,
    penalty_same_time: int = 10,
    penalty_adjacent: int = 3,
    deficit_weight: int = 100,
) -> Tuple[Timetable, List[str]]:
    audit: List[str] = []
    # Simple pass: fill empty teaching cells with any feasible subject already present fewer times in week
    # Build per-grade weekly subject counts
    per_grade_counts: Dict[str, Dict[str, int]] = {g: {} for g in grades}
    for a in tt.all():
        if a.subject in {"Break", "Lunch", "Extra Curricular"}:
            continue
        per_grade_counts.setdefault(a.grade, {})
        per_grade_counts[a.grade][a.subject] = per_grade_counts[a.grade].get(a.subject, 0) + 1

    placed = 0
    for g in grades:
        minima = quotas.minima_for_grade(g) if quotas else {}
        # Compute deficits if quotas provided
        deficits: Dict[str, int] = {}
        if quotas:
            target = quotas.normalized_for_grade(g)
            for subj, tgt in target.items():
                have = per_grade_counts.get(g, {}).get(subj, 0)
                if tgt > have:
                    deficits[subj] = tgt - have
        for d in days:
            for a in list(tt.all()):
                pass  # drain iterator
            # Fill last period T9 first on non-Friday to avoid end-of-day gaps
            last_slots = ["T9"] if d != "Friday" else []
            # then other canonical teaching slots (include all teaching periods)
            scan_slots = last_slots + [f"T{i}" for i in [1, 2, 3, 5, 6, 8, 9]]
            for sid in scan_slots:
                if tt.get(g, d, sid) is not None:
                    continue
                # avoid placing English for B9 outside Wed/Fri
                candidates = [
                    s for s in [
                        "English",
                        "Mathematics",
                        "Science",
                        "Social Studies",
                        "French",
                        "RME",
                        "Computing",
                        "Creative Arts",
                        "Career Tech/Pre-tech",
                        "OWOP",
                        "Twi",
                    ]
                    if not (s == "English" and g.startswith("B9") and d not in {"Wednesday", "Friday"})
                ]
                # Do not repeat same subject in same day
                day_subjects = {x.subject for x in tt.all() if x.grade == g and x.day == d}
                # Allow also subjects beyond hard need (slack) by considering all and filtering later
                candidates = [s for s in candidates if s not in day_subjects]
                # Prefer subjects with deficits first
                def_score = lambda s: deficits.get(s, 0)
                placed_here = False
                for subj in sorted(candidates, key=def_score, reverse=True):
                    # Twi window enforcement for B7–B9
                    if subj == "Twi" and g.startswith(("B7", "B8", "B9")) and d not in {"Wednesday", "Friday"}:
                        continue
                    # Daily uniqueness re-check
                    day_subjects_now = {x.subject for x in tt.all() if x.grade == g and x.day == d}
                    if subj in day_subjects_now:
                        continue
                    teacher = None
                    if teachers:
                        for cand in teachers.candidates_for(subj, g) or [None]:
                            if ledger.can_place(cand, g, d, sid):
                                teacher = cand
                                break
                    if teacher is None:
                        if subj not in {"P.E.", "UCMAS", "Extra Curricular"}:
                            continue
                        if not ledger.can_place(None, g, d, sid):
                            continue
                    # Place
                    tt.place(Assignment(g, d, sid, subj, teacher, False))
                    ledger.place(teacher, g, d, sid)
                    placed += 1
                    audit.append(f"Filled empty {g} {d} {sid} with {subj} – {teacher or ''}")
                    placed_here = True
                    break
        # Replacement-based swaps to reduce deficits
        swaps = 0
        if quotas and teachers and any(deficits.values()):
            # Build day->subjects present to enforce daily uniqueness
            day_subjects_map: Dict[str, set] = {d: {a.subject for a in tt.all() if a.grade == g and a.day == d} for d in days}
            for need_subj, need_cnt in list(deficits.items()):
                if need_cnt <= 0:
                    continue
                for d in days:
                    if need_subj in day_subjects_map.get(d, set()):
                        continue
                    for sid in [f"T{i}" for i in [1, 2, 3, 5, 6, 8]]:
                        a = tt.get(g, d, sid)
                        if a is None or a.immutable:
                            continue
                        if a.subject == need_subj:
                            continue
                        # ensure we don't drop below minima for replaced subject
                        if per_grade_counts.get(g, {}).get(a.subject, 0) <= minima.get(a.subject, 0):
                            continue
                        # time windows for Twi/B9 English
                        if need_subj == "Twi" and g.startswith(("B7", "B8", "B9")) and d not in {"Wednesday", "Friday"}:
                            continue
                        if need_subj == "English" and g.startswith("B9") and d not in {"Wednesday", "Friday"}:
                            continue
                        # teacher availability for needed subject
                        chosen_teacher = None
                        for cand in teachers.candidates_for(need_subj, g) or [None]:
                            # Release current occupancy to test feasibility
                            old_teacher = a.teacher
                            ledger.remove(old_teacher, g, d, sid)
                            ok = ledger.can_place(cand, g, d, sid)
                            # restore before continuing
                            ledger.place(old_teacher, g, d, sid)
                            if ok:
                                chosen_teacher = cand
                                break
                        if chosen_teacher is None and need_subj not in {"P.E.", "UCMAS", "Extra Curricular"}:
                            continue
                        # Final per-day subject uniqueness guard (re-check right before applying)
                        current_day_subjects = {x.subject for x in tt.all() if x.grade == g and x.day == d and (x.day != d or x.slot_id != sid)}
                        if need_subj in current_day_subjects:
                            continue
                        # Perform replacement
                        old_teacher = a.teacher
                        ledger.remove(old_teacher, g, d, sid)
                        tt.place(Assignment(g, d, sid, need_subj, chosen_teacher, False))
                        ledger.place(chosen_teacher, g, d, sid)
                        per_grade_counts[g][a.subject] = per_grade_counts[g].get(a.subject, 1) - 1
                        per_grade_counts[g][need_subj] = per_grade_counts[g].get(need_subj, 0) + 1
                        day_subjects_map[d].discard(a.subject)
                        day_subjects_map[d].add(need_subj)
                        deficits[need_subj] -= 1
                        swaps += 1
                        audit.append(f"Replaced {g} {d} {sid}: {a.subject} -> {need_subj} – {chosen_teacher or ''}")
                        if swaps >= max_swaps or deficits[need_subj] <= 0:
                            break
                    if swaps >= max_swaps or deficits.get(need_subj, 0) <= 0:
                        break
        # Bidirectional swap hill-climb for spacing/concurrency within this grade
        if time_slots is not None and teachers is not None and max_swaps > 0:
            obj_before = _objective(tt, quotas, grades, days, time_slots, penalty_same_time, penalty_adjacent, deficit_weight)
            cells = [a for a in tt.all() if a.grade == g and a.subject not in {"Break", "Lunch", "Extra Curricular"} and not a.immutable]
            for i in range(len(cells)):
                for j in range(i + 1, len(cells)):
                    a1 = cells[i]
                    a2 = cells[j]
                    if a1.day == a2.day and a1.slot_id == a2.slot_id:
                        continue
                    # Daily uniqueness after swap
                    day1_subjects = {x.subject for x in tt.all() if x.grade == g and x.day == a1.day and not (x.day == a1.day and x.slot_id == a1.slot_id)}
                    day2_subjects = {x.subject for x in tt.all() if x.grade == g and x.day == a2.day and not (x.day == a2.day and x.slot_id == a2.slot_id)}
                    if a2.subject in day1_subjects or a1.subject in day2_subjects:
                        continue
                    # Window rules
                    if a2.subject == "Twi" and g.startswith(("B7", "B8", "B9")) and a1.day not in {"Wednesday", "Friday"}:
                        continue
                    if a1.subject == "Twi" and g.startswith(("B7", "B8", "B9")) and a2.day not in {"Wednesday", "Friday"}:
                        continue
                    if a2.subject == "English" and g.startswith("B9") and a1.day not in {"Wednesday", "Friday"}:
                        continue
                    if a1.subject == "English" and g.startswith("B9") and a2.day not in {"Wednesday", "Friday"}:
                        continue
                    # Teacher availability for swapped positions (allow reassignment)
                    new1_teacher = None
                    for cand in teachers.candidates_for(a2.subject, g) or [None]:
                        ledger.remove(a1.teacher, g, a1.day, a1.slot_id)
                        ok = ledger.can_place(cand, g, a1.day, a1.slot_id)
                        ledger.place(a1.teacher, g, a1.day, a1.slot_id)
                        if ok:
                            new1_teacher = cand
                            break
                    if new1_teacher is None and a2.subject not in {"P.E.", "UCMAS", "Extra Curricular"}:
                        continue
                    new2_teacher = None
                    for cand in teachers.candidates_for(a1.subject, g) or [None]:
                        ledger.remove(a2.teacher, g, a2.day, a2.slot_id)
                        ok = ledger.can_place(cand, g, a2.day, a2.slot_id)
                        ledger.place(a2.teacher, g, a2.day, a2.slot_id)
                        if ok:
                            new2_teacher = cand
                            break
                    if new2_teacher is None and a1.subject not in {"P.E.", "UCMAS", "Extra Curricular"}:
                        continue
                    # Allow same-subject concurrency; objective penalizes adjacency/parallelism
                    # Apply tentative swap
                    ledger.remove(a1.teacher, g, a1.day, a1.slot_id)
                    ledger.remove(a2.teacher, g, a2.day, a2.slot_id)
                    tt.place(Assignment(g, a1.day, a1.slot_id, a2.subject, new1_teacher, False))
                    ledger.place(new1_teacher, g, a1.day, a1.slot_id)
                    tt.place(Assignment(g, a2.day, a2.slot_id, a1.subject, new2_teacher, False))
                    ledger.place(new2_teacher, g, a2.day, a2.slot_id)
                    obj_after = _objective(tt, quotas, grades, days, time_slots, penalty_same_time, penalty_adjacent, deficit_weight)
                    if obj_after < obj_before:
                        audit.append(f"Swapped {g} {a1.day} {a1.slot_id} ({a1.subject}) <-> {a2.day} {a2.slot_id} ({a2.subject})")
                        obj_before = obj_after
                        max_swaps -= 1
                        if max_swaps <= 0:
                            return tt, audit
                        # refresh current cells
                        cells[i] = tt.get(g, a1.day, a1.slot_id)
                        cells[j] = tt.get(g, a2.day, a2.slot_id)
                    else:
                        # revert
                        ledger.remove(new1_teacher, g, a1.day, a1.slot_id)
                        ledger.remove(new2_teacher, g, a2.day, a2.slot_id)
                        tt.place(Assignment(g, a1.day, a1.slot_id, a1.subject, a1.teacher, False))
                        ledger.place(a1.teacher, g, a1.day, a1.slot_id)
                        tt.place(Assignment(g, a2.day, a2.slot_id, a2.subject, a2.teacher, False))
                        ledger.place(a2.teacher, g, a2.day, a2.slot_id)
    if placed == 0:
        audit.append("No repairs applied.")
    return tt, audit


def _objective(
    tt: Timetable,
    quotas: SubjectQuotas | None,
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
    penalty_same_time: int,
    penalty_adjacent: int,
    deficit_weight: int,
) -> int:
    # Deficits weighted heavily
    deficits = 0
    if quotas is not None:
        for g in grades:
            target = quotas.normalized_for_grade(g)
            counts: Dict[str, int] = {}
            for a in tt.all():
                if a.grade == g and a.subject not in {"Break", "Lunch", "Extra Curricular", "UCMAS", "P.E."}:
                    counts[a.subject] = counts.get(a.subject, 0) + 1
            for subj, tgt in target.items():
                have = counts.get(subj, 0)
                if have < tgt:
                    deficits += (tgt - have)
    # Penalties for concurrency and adjacency across grades
    pen = 0
    # Same time penalty
    for d in days:
        for s in [t["id"] for t in time_slots if t["type"] == "teaching"]:
            subj_counts: Dict[str, int] = {}
            for a in tt.all():
                if a.day == d and a.slot_id == s and a.subject not in {"Break", "Lunch", "Extra Curricular"}:
                    subj_counts[a.subject] = subj_counts.get(a.subject, 0) + 1
            for c in subj_counts.values():
                if c > 1:
                    pen += (c - 1) * penalty_same_time
    # Adjacency penalty (difference of 1 period across classes for same subject)
    order = {f"T{i}": i for i in range(1, 10)}
    for d in days:
        by_subj: Dict[str, List[int]] = {}
        for a in tt.all():
            if a.day == d and a.subject not in {"Break", "Lunch", "Extra Curricular"}:
                by_subj.setdefault(a.subject, []).append(order.get(a.slot_id, 0))
        for slots in by_subj.values():
            slots.sort()
            for i in range(1, len(slots)):
                if slots[i] - slots[i - 1] == 1:
                    pen += penalty_adjacent
    return deficits * deficit_weight + pen
