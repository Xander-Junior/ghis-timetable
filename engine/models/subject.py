from dataclasses import dataclass


@dataclass(frozen=True)
class Subject:
    name: str
