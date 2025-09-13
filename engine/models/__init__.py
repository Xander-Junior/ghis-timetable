# Re-export common types
from .subject import Subject
from .teacher import Teacher
from .period import TimeSlot
from .grade import Grade
from .assignment import Assignment
from .timetable import Timetable

__all__ = [
    "Subject",
    "Teacher",
    "TimeSlot",
    "Grade",
    "Assignment",
    "Timetable",
]

