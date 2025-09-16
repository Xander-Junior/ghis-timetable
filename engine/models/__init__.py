# Re-export common types
from .assignment import Assignment
from .grade import Grade
from .period import TimeSlot
from .subject import Subject
from .teacher import Teacher
from .timetable import Timetable

__all__ = [
    "Subject",
    "Teacher",
    "TimeSlot",
    "Grade",
    "Assignment",
    "Timetable",
]
