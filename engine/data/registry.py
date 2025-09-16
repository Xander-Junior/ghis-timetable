from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple


@dataclass
class ConstraintRegistry:
    collision_rules: Dict[str, bool]
    weekly_quotas: Dict[str, int]
    time_windows: List[dict]
    pe_policy: Dict[str, object]
    ucmas_policy: Dict[str, object]
    extra_curricular: Dict[str, object]
    anti_patterns: Dict[str, object]
    immutables: Dict[str, bool]


class SubjectQuotas:
    def __init__(self, weekly_quotas: Dict[str, int]):
        self.base = dict(weekly_quotas)
        # Either a global bool, or a targeted set of grades to relax
        self._relax_electives: object = False

    def set_relax_electives(self, flag_or_grades) -> None:
        # Accept bool for global relaxation, or iterable of grades for targeted
        if isinstance(flag_or_grades, bool):
            self._relax_electives = flag_or_grades
        else:
            try:
                self._relax_electives = set(flag_or_grades)
            except Exception:
                self._relax_electives = False

    def _level(self, grade: str) -> int:
        try:
            return int(grade[1])
        except Exception:
            return 9

    def applicable(self, grade: str) -> Dict[str, int]:
        q = dict(self.base)
        level = self._level(grade)
        # Applicability constraints
        if 1 <= level <= 6:
            q.pop("Career Tech/Pre-tech", None)
        if 7 <= level <= 9:
            q.pop("OWOP", None)
        # Normalize UCMAS policy
        if grade.startswith("B9"):
            q["UCMAS"] = 0
        return q

    def normalized_for_grade(self, grade: str) -> Dict[str, int]:
        # Capacity: 6 teaching periods/day * 5 days = 30
        # Excluding non-teaching and fixed Extra Curricular (handled separately)
        capacity_total = 30
        q = self.applicable(grade)
        # Option B: relax elective (non-core) minima by -1 to reduce pressure when enabled
        relax_for_grade = False
        if self._relax_electives is True:
            relax_for_grade = True
        elif isinstance(self._relax_electives, set) and grade in self._relax_electives:
            relax_for_grade = True
        if relax_for_grade:
            non_core = {
                "RME",
                "OWOP",
                "Creative Arts",
                "Computing",
                "Career Tech/Pre-tech",
                "French",
                "Twi",
            }
            for subj in list(q.keys()):
                if subj in non_core:
                    q[subj] = max(0, int(q[subj]) - 1)
        # Reserve seeded items (P.E. always 1, UCMAS for B1–B8)
        reserved = q.get("P.E.", 0) + q.get("UCMAS", 0)
        capacity_fill = capacity_total - reserved
        # Build fill quotas (exclude seeded and non-teaching)
        fill = {k: v for k, v in q.items() if k not in {"P.E.", "UCMAS", "Extra Curricular"}}
        # Priority tiers and minimums
        core = {"English": 4, "Mathematics": 4, "Science": 4}
        level = self._level(grade)
        # Segment-aware minima
        if 1 <= level <= 3:
            mins = {
                "Social Studies": 2,
                "RME": 1,
                "French": 2,
                "Twi": 1,
                "Creative Arts": 1,
                "Computing": 1,
                "OWOP": 1,
            }
        elif 4 <= level <= 6:
            mins = {
                "Social Studies": 2,
                "RME": 2,
                "French": 2,
                "Twi": 1,
                "Creative Arts": 2,
                "Computing": 2,
                "OWOP": 2,
            }
        else:
            mins = {
                "Social Studies": 2,
                "RME": 2,
                "French": 2,
                "Twi": 2,  # enforced double due to two-day window
                "Creative Arts": 2,
                "Computing": 2,
                "Career Tech/Pre-tech": 2,
            }
        # Enforce core values
        for k, v in core.items():
            if k in fill:
                fill[k] = max(v, fill[k])
        # Enforce Twi min 2 for B7–B9 due to hard windows (two days)
        if self._level(grade) >= 7:
            if "Twi" in fill:
                fill["Twi"] = max(2, fill["Twi"])
        # Compute overflow and reduce lower-priority subjects until it fits
        order = [
            "Career Tech/Pre-tech",
            "OWOP",
            "Creative Arts",
            "Computing",
            "RME",
            "Social Studies",
            "French",
            "Twi",
        ]

        def total(d: Dict[str, int]) -> int:
            return sum(d.values())

        while total(fill) > capacity_fill:
            reduced = False
            for subj in order:
                if subj not in fill:
                    continue
                min_v = mins.get(subj, 1)
                if fill[subj] > min_v and total(fill) > capacity_fill:
                    fill[subj] -= 1
                    reduced = True
                if total(fill) <= capacity_fill:
                    break
            if not reduced:
                break
        return fill

    def minima_for_grade(self, grade: str) -> Dict[str, int]:
        # Mirror the minima logic used by normalized_for_grade
        level = self._level(grade)
        if 1 <= level <= 3:
            return {
                "Social Studies": 2,
                "RME": 1,
                "French": 2,
                "Twi": 1,
                "Creative Arts": 1,
                "Computing": 1,
                "OWOP": 1,
            }
        elif 4 <= level <= 6:
            return {
                "Social Studies": 2,
                "RME": 2,
                "French": 2,
                "Twi": 1,
                "Creative Arts": 2,
                "Computing": 2,
                "OWOP": 2,
            }
        else:
            return {
                "Social Studies": 2,
                "RME": 2,
                "French": 2,
                "Twi": 2,
                "Creative Arts": 2,
                "Computing": 2,
                "Career Tech/Pre-tech": 2,
            }

    def maxima_for_grade(self, grade: str) -> Dict[str, int]:
        # Upper bounds to avoid saturating a week with a single non-core subject
        base = self.normalized_for_grade(grade)
        level = self._level(grade)
        maxes: Dict[str, int] = {}
        for subj, tgt in base.items():
            if subj in {"English", "Mathematics", "Science"}:
                maxes[subj] = 4  # keep core at exactly 4
            elif subj == "Twi":
                maxes[subj] = 2  # window enforces exactly 2 for B7–B9
            elif subj in {"UCMAS", "P.E."}:
                maxes[subj] = tgt
            else:
                # safe slack: allow +1 up to 4 max
                maxes[subj] = min(4, tgt + 1)
        return maxes


class OccupancyLedger:
    def __init__(self):
        # Track (teacher, day, slot) and (grade, day, slot)
        self.teacher_busy: Set[Tuple[str, str, str]] = set()
        self.class_busy: Set[Tuple[str, str, str]] = set()

    def can_place(self, teacher: str | None, grade: str, day: str, slot_id: str) -> bool:
        if (grade, day, slot_id) in self.class_busy:
            return False
        if teacher is None:
            return True
        return (teacher, day, slot_id) not in self.teacher_busy

    def place(self, teacher: str | None, grade: str, day: str, slot_id: str) -> None:
        self.class_busy.add((grade, day, slot_id))
        if teacher is not None:
            self.teacher_busy.add((teacher, day, slot_id))

    def remove(self, teacher: str | None, grade: str, day: str, slot_id: str) -> None:
        self.class_busy.discard((grade, day, slot_id))
        if teacher is not None:
            self.teacher_busy.discard((teacher, day, slot_id))
