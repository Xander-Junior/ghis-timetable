from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple, Iterable, List

from .assignment import Assignment


Key = Tuple[str, str, str]  # (grade, day, slot_id)


@dataclass
class Timetable:
    cells: Dict[Key, Assignment] = field(default_factory=dict)

    def place(self, a: Assignment) -> None:
        self.cells[(a.grade, a.day, a.slot_id)] = a

    def get(self, grade: str, day: str, slot_id: str) -> Assignment | None:
        return self.cells.get((grade, day, slot_id))

    def occupied(self, grade: str, day: str, slot_id: str) -> bool:
        return (grade, day, slot_id) in self.cells

    def iter_grade(self, grade: str) -> Iterable[Assignment]:
        for (g, _, _), a in self.cells.items():
            if g == grade:
                yield a

    def all(self) -> Iterable[Assignment]:
        return self.cells.values()

    def remove(self, grade: str, day: str, slot_id: str) -> None:
        self.cells.pop((grade, day, slot_id), None)

    def slots_for(self, grade: str, day: str) -> List[str]:
        return [sid for (g, d, sid) in self.cells if g == grade and d == day]

