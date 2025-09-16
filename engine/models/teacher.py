from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Teacher:
    id: str
    name: str
    subjects: List[str]
    grades: List[str]
    role: str | None = None
