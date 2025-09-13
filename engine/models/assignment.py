from dataclasses import dataclass


@dataclass
class Assignment:
    grade: str
    day: str
    slot_id: str
    subject: str
    teacher: str | None
    immutable: bool = False

