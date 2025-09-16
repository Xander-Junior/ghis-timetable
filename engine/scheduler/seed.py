from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Tuple

from ..data.registry import OccupancyLedger
from ..data.teachers import TeacherDirectory
from ..models.assignment import Assignment
from ..models.timetable import Timetable


def seed_schedule(
    tt: Timetable,
    ledger: OccupancyLedger,
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
    constraints: Dict[str, object],
    teachers: TeacherDirectory,
) -> Tuple[Timetable, List[str]]:
    logger = logging.getLogger(__name__)
    audit: List[str] = []

    slots_by_id = {s["id"]: s for s in time_slots}
    teaching_slots = [s for s in time_slots if s["type"] == "teaching"]

    # Seed Break and Lunch (immutable)
    for day in days:
        for s in time_slots:
            if s["type"] in {"break", "lunch"}:
                for g in grades:
                    a = Assignment(g, day, s["id"], s.get("label", s["type"]).title(), None, True)
                    tt.place(a)
                    ledger.place(None, g, day, s["id"])  # non-teaching
                    logger.info(f"Seed {g} {day} {s['id']} -> {a.subject}")
    audit.append("Seeded Break/Lunch across all grades and days.")

    # Seed Extra Curricular at T9 on Friday only (immutable)
    for s in teaching_slots:
        if s.get("id") == "T9":
            day = "Friday"
            for g in grades:
                a = Assignment(g, day, s["id"], "Extra Curricular", None, True)
                tt.place(a)
                ledger.place(None, g, day, s["id"])  # Teacher not required
                logger.info(f"Seed {g} {day} {s['id']} -> {a.subject}")
    audit.append("Seeded Extra Curricular in T9 on Friday only.")

    # P.E. policy: Friday, different periods by segment (Lower, Upper, JHS)
    # Segments: Lower=B1–B3 -> choose T1; Upper=B4–B6 -> choose T2; JHS=B7–B9 -> choose T3
    pe_day = constraints.get("pe_policy", {}).get("day", "Friday")
    # Choose non-overlapping across segments and avoid Twi windows for JHS (Fri T3)
    pe_slots = {"lower": "T1", "upper": "T2", "jhs": "T8"}
    for g in grades:
        seg = segment_of_grade(g)
        sid = pe_slots[seg]
        a = Assignment(g, pe_day, sid, "P.E.", None, True)
        if tt.get(g, pe_day, sid) is None:
            tt.place(a)
            ledger.place(None, g, pe_day, sid)
            logger.info(f"Seed {g} {pe_day} {sid} -> P.E.")
    audit.append("Seeded P.E. on Friday with distinct periods per segment.")

    # Twi windows: B7–B9 Wednesday & Friday
    twi_days = [tw for tw in constraints.get("time_windows", []) if tw.get("subject") == "Twi"]
    twi_days_set = set()
    for tw in twi_days:
        if tw.get("hard"):
            for d in tw.get("days", []):
                twi_days_set.add(d)
    # Stagger across grades: B7->T1, B8->T2, B9->T3 on Wed and Fri
    twi_map = {"B7": "T1", "B8": "T2", "B9": "T3"}
    for g in [
        gr for gr in grades if gr.startswith("B7") or gr.startswith("B8") or gr.startswith("B9")
    ]:
        base = g[:2]
        sid = twi_map.get(base, "T1")
        for d in twi_days_set:
            a = Assignment(g, d, sid, "Twi", teachers.teacher_for("Twi", g), True)
            if tt.get(g, d, sid) is None and ledger.can_place(a.teacher, g, d, sid):
                tt.place(a)
                ledger.place(a.teacher, g, d, sid)
                logger.info(f"Seed {g} {d} {sid} -> Twi – {a.teacher}")
    audit.append("Seeded Twi (B7–B9) on Wed/Fri with staggering.")

    # B9 English Wed/Fri double periods consecutive with Mr. Dey else Harriet
    # We choose T5+T6 as the consecutive slots
    for g in [gr for gr in grades if gr.startswith("B9")]:
        for d in ["Wednesday", "Friday"]:
            teacher = teachers.preferred_english_teacher_b9(d, ["T5", "T6"], ledger)
            for sid in ["T5", "T6"]:
                a = Assignment(g, d, sid, "English", teacher, True)
                if tt.get(g, d, sid) is None and ledger.can_place(teacher, g, d, sid):
                    tt.place(a)
                    ledger.place(teacher, g, d, sid)
                    logger.info(f"Seed {g} {d} {sid} -> English – {teacher}")
    audit.append("Seeded B9 English double periods on Wed/Fri (T5+T6).")

    # UCMAS once/week for B1–B8, ensure different periods if same day and min gap
    ucmas_grades = [
        g
        for g in grades
        if g.startswith("B1")
        or g.startswith("B2")
        or g.startswith("B3")
        or g.startswith("B4")
        or g.startswith("B5")
        or g.startswith("B6")
        or g.startswith("B7")
        or g.startswith("B8")
    ]
    # Place on Tuesday, stagger periods T1,T3,T5,T8 cyclically
    uc_slots = ["T1", "T3", "T5", "T8"]
    # Allow override via constraints override key if present
    day = constraints.get("ucmas_policy", {}).get("day_override", "Tuesday")
    used_slots: List[str] = []
    for idx, g in enumerate(sorted(ucmas_grades)):
        sid = uc_slots[idx % len(uc_slots)]
        # ensure a gap of >=1 period across classes on same day
        if used_slots and sid in used_slots:
            sid = uc_slots[(idx + 1) % len(uc_slots)]
        used_slots.append(sid)
        a = Assignment(g, day, sid, "UCMAS", None, True)
        if tt.get(g, day, sid) is None and ledger.can_place(None, g, day, sid):
            tt.place(a)
            ledger.place(None, g, day, sid)
            logger.info(f"Seed {g} {day} {sid} -> UCMAS")
    audit.append("Seeded UCMAS once/week (Tue) with gaps across classes.")

    return tt, audit


def segment_of_grade(grade: str) -> str:
    if grade.startswith("B1") or grade.startswith("B2") or grade.startswith("B3"):
        return "lower"
    if grade.startswith("B4") or grade.startswith("B5") or grade.startswith("B6"):
        return "upper"
    return "jhs"


## teacher mapping now provided by TeacherDirectory
