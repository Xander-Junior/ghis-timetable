from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class TeacherRecord:
    id: str
    name: str
    subjects: List[str]
    grades: List[str]
    role: str | None = None


class TeacherDirectory:
    def __init__(self, data: Dict[str, object]):
        self.records: List[TeacherRecord] = []
        for t in data.get("teachers", []):
            self.records.append(
                TeacherRecord(
                    id=t.get("id"),
                    name=t.get("name"),
                    subjects=list(t.get("subjects", [])),
                    grades=list(t.get("grades", [])),
                    role=t.get("role"),
                )
            )

        # Class-teacher mapping for B1–B5 (fallbacks)
        self.class_teachers: Dict[str, str] = {
            "B1": "Mrs. Theodora Amoah",
            "B2A": "Mr. Isaac Appiah",
            "B2B": "Mrs. Charity Hayford",
            "B3": "Mr. Welbeck Konor",
            "B4A": "Miss Anita Twumasi",
            "B4B": "Mr. Cyril Anani",
            "B5A": "Mr. Mark Mossie",
            "B5B": "Mr. Enoch Asare",
        }

    def candidates_for(self, subject: str, grade: str) -> List[str]:
        # Grade prefix matching: "B6A" matches record with grade "B6"
        pref = grade[:2]
        out: List[str] = []
        for r in self.records:
            if subject in r.subjects and any(pref.startswith(g) or pref == g for g in r.grades):
                out.append(r.name)
        # Fallback to class teacher for B1–B5 for general subjects
        if grade.startswith(("B1", "B2", "B3", "B4", "B5")):
            ct = self.class_teachers.get(grade)
            general = {"English", "Mathematics", "Science", "Social Studies", "RME", "Creative Arts", "OWOP"}
            if ct and subject in general and ct not in out:
                out.append(ct)
        return out

    def teacher_for(self, subject: str, grade: str) -> str | None:
        # Special case: B6–B9 English prefers Mr. Bright Dey (primary)
        if subject == "English" and grade.startswith(("B6", "B7", "B8", "B9")):
            prim = [r.name for r in self.records if r.name == "Mr. Bright Dey"]
            if prim:
                return prim[0]
        cands = self.candidates_for(subject, grade)
        return cands[0] if cands else None

    def preferred_english_teacher_b9(self, day: str, slots: List[str], ledger) -> str:
        # Prefer Dey unless busy in any of the requested slots; else Harriet
        dey = "Mr. Bright Dey"
        if all((dey, day, sid) not in getattr(ledger, "teacher_busy", set()) for sid in slots):
            return dey
        return "Miss Harriet Akasraku"
