from __future__ import annotations

from typing import Dict, List, Tuple

AM_SLOTS = {"T1", "T2", "T3"}
PM_SLOTS = {"T5", "T6", "T8"}


def score_candidate(
    *,
    grade: str,
    day: str,
    slot_id: str,
    subject: str,
    teacher: str | None,
    existing_day_subjects: List[str],
    same_time_subjects_across_grades: List[str],
    english_pref_days: List[str] | None,
    weekly_counts: Dict[str, int] | None = None,
    min_gap_to_others: int | None = None,
) -> int:
    s = 0
    # Prefer one instance per day
    if subject not in existing_day_subjects:
        s += 5
    # No immediate repeat handled upstream; small bonus for spreading
    s += 1
    # Preference: B7–B8 English on Wed/Fri
    if subject == "English" and english_pref_days and day in english_pref_days:
        s += 4
    # Penalize same subject same time across classes (last resort)
    if subject in same_time_subjects_across_grades:
        s -= 10
    # Week spread: prefer subjects with lower weekly count so far
    if weekly_counts is not None:
        used = weekly_counts.get(subject, 0)
        s += max(0, 3 - used)  # diminishing bonus
    # AM/PM balance: prefer alternating halves
    if slot_id in AM_SLOTS:
        s += 1
    elif slot_id in PM_SLOTS:
        s += 1
    # Cross-grade min-gap preference
    if min_gap_to_others is not None:
        if min_gap_to_others >= 2:
            s += 2
        elif min_gap_to_others == 1:
            s -= 3
    return s
