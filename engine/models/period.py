from dataclasses import dataclass


@dataclass(frozen=True)
class TimeSlot:
    id: str
    start: str
    end: str
    type: str  # teaching, break, lunch
    label: str | None = None
    fixed_subject: str | None = None

