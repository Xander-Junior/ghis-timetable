from __future__ import annotations

import logging
from collections import defaultdict, Counter
from typing import Dict, List, Tuple

from ..models.assignment import Assignment
from ..models.timetable import Timetable
from ..data.registry import OccupancyLedger, SubjectQuotas
from ..data.teachers import TeacherDirectory
from .score import score_candidate


def build_need_lists(grades: List[str], quotas: SubjectQuotas) -> Dict[str, Counter]:
    needs: Dict[str, Counter] = {}
    for g in grades:
        q = quotas.normalized_for_grade(g)
        c = Counter()
        for subj, times in q.items():
            if subj == "UCMAS_B1_B8" or subj == "UCMAS_B9":
                # Normalize to UCMAS subject key
                continue
            if subj == "P.E.":
                # already seeded once/week
                continue
            if subj == "UCMAS":
                continue
            c[subj] += int(times)
        needs[g] = c
    return needs


def subtract_seeded(needs: Dict[str, Counter], tt: "Timetable") -> Dict[str, Counter]:
    placed = defaultdict(Counter)
    for a in tt.all():
        if a.subject in {"Break", "Lunch", "Extra Curricular", "UCMAS", "P.E."}:
            continue
        placed[a.grade][a.subject] += 1
    for g, ctr in needs.items():
        for subj, rem in list(ctr.items()):
            have = placed[g].get(subj, 0)
            new_rem = max(0, rem - have)
            ctr[subj] = new_rem
    return needs


def remaining_open_slots(time_slots: List[dict]) -> List[str]:
    # Teaching slots excluding T4/T7 break/lunch
    return [s["id"] for s in time_slots if s["type"] == "teaching"]


def fill_schedule(
    tt: Timetable,
    ledger: OccupancyLedger,
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
    quotas: SubjectQuotas,
    teachers: TeacherDirectory,
) -> Tuple[Timetable, List[str]]:
    logger = logging.getLogger(__name__)
    audit: List[str] = []

    needs = build_need_lists(grades, quotas)
    needs = subtract_seeded(needs, tt)
    teaching_slots = remaining_open_slots(time_slots)
    order_index = {f"T{i}": i for i in range(1, 10)}

    # Remove any fixed-subject slot (Extra Curricular) from fill consideration
    fixed_ids = {s["id"] for s in time_slots if s.get("fixed_subject")}

    # Track weekly counts for spread scoring
    weekly_counts: Dict[str, Counter] = {g: Counter() for g in grades}
    for a in tt.all():
        if a.subject not in {"Break", "Lunch", "Extra Curricular", "UCMAS", "P.E."}:
            weekly_counts.setdefault(a.grade, Counter())[a.subject] += 1

    # Optional maxima per grade (not enforced as hard, removed to reduce gaps)
    # maxima: Dict[str, Dict[str, int]] = {g: quotas.maxima_for_grade(g) for g in grades}

    for g in grades:
        # Fill by iterating days and slots in a simple round-robin
        for day in days:
            # Collect already placed subjects for the day
            existing_day_subjects = {a.subject for a in tt.all() if a.grade == g and a.day == day}
            for sid in teaching_slots:
                if sid in fixed_ids:
                    continue
                if tt.get(g, day, sid) is not None:
                    continue
                # pick best subject candidate
                best: Tuple[int, str, str | None] | None = None
                same_time_subjects = [a.subject for a in tt.all() if a.day == day and a.slot_id == sid]
                for subj, rem in list(needs[g].items()):
                    if rem <= 0:
                        continue
                    # Allow same-subject concurrency across grades as last resort; penalized in scoring
                    # Enforce Twi windows for B7–B9
                    if subj == "Twi" and (g.startswith("B7") or g.startswith("B8") or g.startswith("B9")) and day not in {"Wednesday", "Friday"}:
                        continue
                    # Prevent immediate repeat within same day
                    if subj in existing_day_subjects:
                        continue
                    # B9 English allowed only Wed/Fri (double seeded), skip others
                    if subj == "English" and g.startswith("B9") and day not in {"Wednesday", "Friday"}:
                        continue
                    # choose first available teacher candidate
                    chosen_teacher: str | None = None
                    for cand in teachers.candidates_for(subj, g) or [None]:
                        if ledger.can_place(cand, g, day, sid):
                            chosen_teacher = cand
                            break
                    if chosen_teacher is None:
                        # Only allow teacherless for special subjects
                        if subj not in {"P.E.", "UCMAS", "Extra Curricular"}:
                            continue
                        if not ledger.can_place(None, g, day, sid):
                            continue
                    english_prefs = ["Wednesday", "Friday"] if (g.startswith("B7") or g.startswith("B8")) else None
                    # compute min gap to other classes teaching same subject that day
                    other_slots = [order_index.get(x.slot_id, 0) for x in tt.all() if x.day == day and x.subject == subj]
                    sid_idx = order_index.get(sid, 0)
                    min_gap = None
                    if other_slots:
                        min_gap = min(abs(sid_idx - o) for o in other_slots)
                    sc = score_candidate(
                        grade=g,
                        day=day,
                        slot_id=sid,
                        subject=subj,
                        teacher=chosen_teacher,
                        existing_day_subjects=list(existing_day_subjects),
                        same_time_subjects_across_grades=same_time_subjects,
                        english_pref_days=english_prefs,
                        weekly_counts=dict(weekly_counts.get(g, {})),
                        min_gap_to_others=min_gap,
                    )
                    cand = (sc + subject_priority(subj), subj, chosen_teacher)
                    if best is None or cand[0] > best[0]:
                        best = cand
                if best is not None:
                    _, subj, teacher = best
                    a = Assignment(g, day, sid, subj, teacher, False)
                    tt.place(a)
                    ledger.place(teacher, g, day, sid)
                    needs[g][subj] -= 1
                    existing_day_subjects.add(subj)
                    logger.info(f"Fill {g} {day} {sid} -> {subj} – {teacher}")
                    if subj not in {"Break", "Lunch", "Extra Curricular", "UCMAS", "P.E."}:
                        weekly_counts[g][subj] += 1
                    continue
                # Second chance: consider slack subjects (beyond hard need) with soft maxima
                best = None
                used = weekly_counts.get(g, Counter())
                soft_max = _soft_max_for_grade(quotas, g)
                universe = [
                    "English","Mathematics","Science","Social Studies","French","RME",
                    "Computing","Creative Arts","Career Tech/Pre-tech","OWOP","Twi"
                ]
                for subj in universe:
                    # skip fixed-only or seeded-only
                    if subj in {"P.E.", "UCMAS", "Extra Curricular"}:
                        continue
                    # windows
                    if subj == "Twi" and (g.startswith("B7") or g.startswith("B8") or g.startswith("B9")) and day not in {"Wednesday", "Friday"}:
                        continue
                    if subj == "English" and g.startswith("B9") and day not in {"Wednesday", "Friday"}:
                        continue
                    if subj in existing_day_subjects:
                        continue
                    if used.get(subj, 0) >= soft_max.get(subj, 4):
                        continue
                    chosen_teacher: str | None = None
                    for cand in teachers.candidates_for(subj, g) or [None]:
                        if ledger.can_place(cand, g, day, sid):
                            chosen_teacher = cand
                            break
                    if chosen_teacher is None:
                        continue
                    same_time_subjects = [a.subject for a in tt.all() if a.day == day and a.slot_id == sid]
                    english_prefs = ["Wednesday", "Friday"] if (g.startswith("B7") or g.startswith("B8")) else None
                    other_slots = [order_index.get(x.slot_id, 0) for x in tt.all() if x.day == day and x.subject == subj]
                    sid_idx = order_index.get(sid, 0)
                    min_gap = None
                    if other_slots:
                        min_gap = min(abs(sid_idx - o) for o in other_slots)
                    sc = score_candidate(
                        grade=g,
                        day=day,
                        slot_id=sid,
                        subject=subj,
                        teacher=chosen_teacher,
                        existing_day_subjects=list(existing_day_subjects),
                        same_time_subjects_across_grades=same_time_subjects,
                        english_pref_days=english_prefs,
                        weekly_counts=dict(weekly_counts.get(g, {})),
                        min_gap_to_others=min_gap,
                    ) + subject_priority(subj)
                    cand = (sc, subj, chosen_teacher)
                    if best is None or cand[0] > best[0]:
                        best = cand
                if best is not None:
                    _, subj, teacher = best
                    a = Assignment(g, day, sid, subj, teacher, False)
                    tt.place(a)
                    ledger.place(teacher, g, day, sid)
                    existing_day_subjects.add(subj)
                    logger.info(f"Fill(slack) {g} {day} {sid} -> {subj} – {teacher}")
                    weekly_counts[g][subj] = weekly_counts[g].get(subj, 0) + 1
    audit.append("Filled remaining slots with quota-aware incremental placement.")
    return tt, audit


def subject_priority(subj: str) -> int:
    core = {"English", "Mathematics", "Science"}
    if subj in core:
        return 6
    if subj == "Social Studies":
        return 5
    if subj in {"French", "Twi"}:
        return 4
    if subj in {"Computing", "Career Tech/Pre-tech"}:
        return 3
    if subj == "Creative Arts":
        return 2
    if subj in {"RME", "OWOP"}:
        return 2
    return 0


def _soft_max_for_grade(quotas: SubjectQuotas, grade: str) -> Dict[str, int]:
    hard = quotas.normalized_for_grade(grade)
    soft: Dict[str, int] = {}
    for subj, tgt in hard.items():
        if subj in {"English", "Mathematics", "Science"}:
            soft[subj] = 5
        elif subj == "Social Studies":
            soft[subj] = min(4, tgt + 1)
        else:
            soft[subj] = min(4, tgt + 1)
    return soft
