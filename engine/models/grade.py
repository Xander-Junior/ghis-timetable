from dataclasses import dataclass


@dataclass(frozen=True)
class Grade:
    id: str  # e.g., B1, B2A

