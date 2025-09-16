from __future__ import annotations

import random
from collections import Counter, defaultdict, deque
from typing import Dict, Iterable, List, Set, Tuple

from .. import costs as costmod
from ..data.registry import OccupancyLedger, SubjectQuotas
from ..data.teachers import TeacherDirectory
from ..models.assignment import Assignment
from ..models.timetable import Timetable


def _enforce_b9_fri_t9_english(
    tt: Timetable,
    ledger: OccupancyLedger,
    grades: List[str],
    teachers: TeacherDirectory,
    audit: List[str],
) -> None:
    # Ensure B9 Friday T9 is English, and forbid Extra Curricular there
    for g in grades:
        if not g.startswith("B9"):
            continue
        day = "Friday"
        sid = "T9"
        a = tt.get(g, day, sid)
        if a is not None and a.subject == "English":
            continue
        # Remove any existing assignment (e.g., Extra Curricular)
        if a is not None:
            ledger.remove(a.teacher, g, day, sid)
            tt.remove(g, day, sid)
        # Pick English teacher and place
        teacher = teachers.teacher_for("English", g)
        if ledger.can_place(teacher, g, day, sid):
            tt.place(Assignment(g, day, sid, "English", teacher, True))
            ledger.place(teacher, g, day, sid)
            audit.append(f"Enforced seed: {g} {day} {sid} -> English – {teacher}")
        else:
            # Fall back to place without teacher (should not happen for English, but avoid blank)
            tt.place(Assignment(g, day, sid, "English", None, True))
            ledger.place(None, g, day, sid)
            audit.append(f"Enforced seed without teacher: {g} {day} {sid} -> English")


def repair_schedule(
    tt: Timetable,
    ledger: OccupancyLedger,
    grades: List[str],
    days: List[str],
    teachers: TeacherDirectory | None = None,
    quotas: SubjectQuotas | None = None,
    max_swaps: int = 100,
    time_slots: List[dict] | None = None,
    penalty_same_time: int = 10,
    penalty_adjacent: int = 3,
    deficit_weight: int = 100,
    neighborhoods: Iterable[str] | None = None,
    tabu_k: int = 0,
    rng: random.Random | None = None,
    weights: costmod.CostWeights | None = None,
    # Neighborhood bound overrides (optional; defaults keep current behaviour)
    rr_depth: int | None = None,
    rr_nodes: int | None = None,
    rr_attempts_per_blank: int | None = None,
    kempe_depth: int | None = None,
    kempe_nodes: int | None = None,
) -> Tuple[Timetable, List[str]]:
    audit: List[str] = []
    if time_slots is None:
        time_slots = []
    neighborhoods = set(
        neighborhoods
        or ["grade_day", "grade_period", "stuck_grade", "blank_rr", "kempe_period_swap"]
    )
    _rng = rng  # local alias; may be None -> fallback to random module

    # Tabu: recent (grade, day, slot, subject) moves and (swap) patterns
    tabu_cells: deque = deque(maxlen=max(0, tabu_k))
    tabu_swaps: deque = deque(maxlen=max(0, tabu_k))

    # Seed enforcement: B9 Friday T9 must be English; never Extra Curricular at that cell
    if teachers is not None:
        _enforce_b9_fri_t9_english(tt, ledger, grades, teachers, audit)
    # Simple pass: fill empty teaching cells with any feasible subject already present fewer times in week
    # Build per-grade weekly subject counts
    per_grade_counts: Dict[str, Dict[str, int]] = {g: {} for g in grades}
    for a in tt.all():
        if a.subject in {"Break", "Lunch", "Extra Curricular"}:
            continue
        per_grade_counts.setdefault(a.grade, {})
        per_grade_counts[a.grade][a.subject] = per_grade_counts[a.grade].get(a.subject, 0) + 1

    placed = 0
    for g in grades:
        minima = quotas.minima_for_grade(g) if quotas else {}
        # Compute deficits if quotas provided
        deficits: Dict[str, int] = {}
        if quotas:
            target = quotas.normalized_for_grade(g)
            for subj, tgt in target.items():
                have = per_grade_counts.get(g, {}).get(subj, 0)
                if tgt > have:
                    deficits[subj] = tgt - have
        for d in days:
            for a in list(tt.all()):
                pass  # drain iterator
            # Fill last period T9 first on non-Friday to avoid end-of-day gaps
            last_slots = ["T9"] if d != "Friday" else []
            # then other canonical teaching slots (include all teaching periods)
            scan_slots = last_slots + [f"T{i}" for i in [1, 2, 3, 5, 6, 8, 9]]
            for sid in scan_slots:
                if tt.get(g, d, sid) is not None:
                    continue
                # avoid placing English for B9 outside Wed/Fri
                candidates = [
                    s
                    for s in [
                        "English",
                        "Mathematics",
                        "Science",
                        "Social Studies",
                        "French",
                        "RME",
                        "Computing",
                        "Creative Arts",
                        "Career Tech/Pre-tech",
                        "OWOP",
                        "Twi",
                    ]
                    if not (
                        s == "English" and g.startswith("B9") and d not in {"Wednesday", "Friday"}
                    )
                ]
                # Do not repeat same subject in same day
                day_subjects = {x.subject for x in tt.all() if x.grade == g and x.day == d}
                # Allow also subjects beyond hard need (slack) by considering all and filtering later
                candidates = [s for s in candidates if s not in day_subjects]
                # Prefer subjects with deficits first
                def_score = lambda s: deficits.get(s, 0)
                placed_here = False
                for subj in sorted(candidates, key=def_score, reverse=True):
                    # Twi window enforcement for B7–B9
                    if (
                        subj == "Twi"
                        and g.startswith(("B7", "B8", "B9"))
                        and d not in {"Wednesday", "Friday"}
                    ):
                        continue
                    # Daily uniqueness re-check
                    day_subjects_now = {x.subject for x in tt.all() if x.grade == g and x.day == d}
                    if subj in day_subjects_now:
                        continue
                    teacher = None
                    if teachers:
                        for cand in teachers.candidates_for(subj, g) or [None]:
                            if ledger.can_place(cand, g, d, sid):
                                teacher = cand
                                break
                    if teacher is None:
                        if subj not in {"P.E.", "UCMAS", "Extra Curricular"}:
                            continue
                        if not ledger.can_place(None, g, d, sid):
                            continue
                    # Place
                    tt.place(Assignment(g, d, sid, subj, teacher, False))
                    ledger.place(teacher, g, d, sid)
                    placed += 1
                    audit.append(f"Filled empty {g} {d} {sid} with {subj} – {teacher or ''}")
                    placed_here = True
                    break
        # Replacement-based swaps to reduce deficits
        swaps = 0
        if quotas and teachers and any(deficits.values()):
            # Build day->subjects present to enforce daily uniqueness
            day_subjects_map: Dict[str, set] = {
                d: {a.subject for a in tt.all() if a.grade == g and a.day == d} for d in days
            }
            for need_subj, need_cnt in list(deficits.items()):
                if need_cnt <= 0:
                    continue
                for d in days:
                    if need_subj in day_subjects_map.get(d, set()):
                        continue
                    for sid in [f"T{i}" for i in [1, 2, 3, 5, 6, 8]]:
                        a = tt.get(g, d, sid)
                        if a is None or a.immutable:
                            continue
                        if a.subject == need_subj:
                            continue
                        # ensure we don't drop below minima for replaced subject
                        if per_grade_counts.get(g, {}).get(a.subject, 0) <= minima.get(
                            a.subject, 0
                        ):
                            continue
                        # time windows for Twi/B9 English
                        if (
                            need_subj == "Twi"
                            and g.startswith(("B7", "B8", "B9"))
                            and d not in {"Wednesday", "Friday"}
                        ):
                            continue
                        if (
                            need_subj == "English"
                            and g.startswith("B9")
                            and d not in {"Wednesday", "Friday"}
                        ):
                            continue
                        # teacher availability for needed subject
                        chosen_teacher = None
                        for cand in teachers.candidates_for(need_subj, g) or [None]:
                            # Release current occupancy to test feasibility
                            old_teacher = a.teacher
                            ledger.remove(old_teacher, g, d, sid)
                            ok = ledger.can_place(cand, g, d, sid)
                            # restore before continuing
                            ledger.place(old_teacher, g, d, sid)
                            if ok:
                                chosen_teacher = cand
                                break
                        if chosen_teacher is None and need_subj not in {
                            "P.E.",
                            "UCMAS",
                            "Extra Curricular",
                        }:
                            continue
                        # Final per-day subject uniqueness guard (re-check right before applying)
                        current_day_subjects = {
                            x.subject
                            for x in tt.all()
                            if x.grade == g and x.day == d and (x.day != d or x.slot_id != sid)
                        }
                        if need_subj in current_day_subjects:
                            continue
                        # Perform replacement
                        old_teacher = a.teacher
                        ledger.remove(old_teacher, g, d, sid)
                        tt.place(Assignment(g, d, sid, need_subj, chosen_teacher, False))
                        ledger.place(chosen_teacher, g, d, sid)
                        per_grade_counts[g][a.subject] = per_grade_counts[g].get(a.subject, 1) - 1
                        per_grade_counts[g][need_subj] = per_grade_counts[g].get(need_subj, 0) + 1
                        day_subjects_map[d].discard(a.subject)
                        day_subjects_map[d].add(need_subj)
                        deficits[need_subj] -= 1
                        swaps += 1
                        audit.append(
                            f"Replaced {g} {d} {sid}: {a.subject} -> {need_subj} – {chosen_teacher or ''}"
                        )
                        if swaps >= max_swaps or deficits[need_subj] <= 0:
                            break
                    if swaps >= max_swaps or deficits.get(need_subj, 0) <= 0:
                        break
        # Bidirectional swap hill-climb for spacing/concurrency within this grade
        if time_slots is not None and teachers is not None and max_swaps > 0:
            obj_before = _objective(
                tt,
                quotas,
                grades,
                days,
                time_slots,
                penalty_same_time,
                penalty_adjacent,
                deficit_weight,
            )
            cells = [
                a
                for a in tt.all()
                if a.grade == g
                and a.subject not in {"Break", "Lunch", "Extra Curricular"}
                and not a.immutable
            ]
            for i in range(len(cells)):
                for j in range(i + 1, len(cells)):
                    a1 = cells[i]
                    a2 = cells[j]
                    if a1.day == a2.day and a1.slot_id == a2.slot_id:
                        continue
                    # Daily uniqueness after swap
                    day1_subjects = {
                        x.subject
                        for x in tt.all()
                        if x.grade == g
                        and x.day == a1.day
                        and not (x.day == a1.day and x.slot_id == a1.slot_id)
                    }
                    day2_subjects = {
                        x.subject
                        for x in tt.all()
                        if x.grade == g
                        and x.day == a2.day
                        and not (x.day == a2.day and x.slot_id == a2.slot_id)
                    }
                    if a2.subject in day1_subjects or a1.subject in day2_subjects:
                        continue
                    # Window rules
                    if (
                        a2.subject == "Twi"
                        and g.startswith(("B7", "B8", "B9"))
                        and a1.day not in {"Wednesday", "Friday"}
                    ):
                        continue
                    if (
                        a1.subject == "Twi"
                        and g.startswith(("B7", "B8", "B9"))
                        and a2.day not in {"Wednesday", "Friday"}
                    ):
                        continue
                    if (
                        a2.subject == "English"
                        and g.startswith("B9")
                        and a1.day not in {"Wednesday", "Friday"}
                    ):
                        continue
                    if (
                        a1.subject == "English"
                        and g.startswith("B9")
                        and a2.day not in {"Wednesday", "Friday"}
                    ):
                        continue
                    # Teacher availability for swapped positions (allow reassignment)
                    new1_teacher = None
                    for cand in teachers.candidates_for(a2.subject, g) or [None]:
                        ledger.remove(a1.teacher, g, a1.day, a1.slot_id)
                        ok = ledger.can_place(cand, g, a1.day, a1.slot_id)
                        ledger.place(a1.teacher, g, a1.day, a1.slot_id)
                        if ok:
                            new1_teacher = cand
                            break
                    if new1_teacher is None and a2.subject not in {
                        "P.E.",
                        "UCMAS",
                        "Extra Curricular",
                    }:
                        continue
                    new2_teacher = None
                    for cand in teachers.candidates_for(a1.subject, g) or [None]:
                        ledger.remove(a2.teacher, g, a2.day, a2.slot_id)
                        ok = ledger.can_place(cand, g, a2.day, a2.slot_id)
                        ledger.place(a2.teacher, g, a2.day, a2.slot_id)
                        if ok:
                            new2_teacher = cand
                            break
                    if new2_teacher is None and a1.subject not in {
                        "P.E.",
                        "UCMAS",
                        "Extra Curricular",
                    }:
                        continue
                    # Allow same-subject concurrency; objective penalizes adjacency/parallelism
                    # Apply tentative swap
                    ledger.remove(a1.teacher, g, a1.day, a1.slot_id)
                    ledger.remove(a2.teacher, g, a2.day, a2.slot_id)
                    tt.place(Assignment(g, a1.day, a1.slot_id, a2.subject, new1_teacher, False))
                    ledger.place(new1_teacher, g, a1.day, a1.slot_id)
                    tt.place(Assignment(g, a2.day, a2.slot_id, a1.subject, new2_teacher, False))
                    ledger.place(new2_teacher, g, a2.day, a2.slot_id)
                    obj_after = _objective(
                        tt,
                        quotas,
                        grades,
                        days,
                        time_slots,
                        penalty_same_time,
                        penalty_adjacent,
                        deficit_weight,
                    )
                    if obj_after < obj_before:
                        audit.append(
                            f"Swapped {g} {a1.day} {a1.slot_id} ({a1.subject}) <-> {a2.day} {a2.slot_id} ({a2.subject})"
                        )
                        obj_before = obj_after
                        max_swaps -= 1
                        if max_swaps <= 0:
                            return tt, audit
                        # refresh current cells
                        cells[i] = tt.get(g, a1.day, a1.slot_id)
                        cells[j] = tt.get(g, a2.day, a2.slot_id)
                    else:
                        # revert
                        ledger.remove(new1_teacher, g, a1.day, a1.slot_id)
                        ledger.remove(new2_teacher, g, a2.day, a2.slot_id)
                        tt.place(Assignment(g, a1.day, a1.slot_id, a1.subject, a1.teacher, False))
                        ledger.place(a1.teacher, g, a1.day, a1.slot_id)
                        tt.place(Assignment(g, a2.day, a2.slot_id, a2.subject, a2.teacher, False))
                        ledger.place(a2.teacher, g, a2.day, a2.slot_id)
    # LNS / Guided improvements
    # Objective now considers blanks, conflicts, windows, adjacency and dispersion via engine.costs
    base_weights = weights or costmod.load_weights(None)
    order = {
        s["id"]: i
        for i, s in enumerate(
            [t for t in (time_slots or []) if t["type"] in {"teaching", "break", "lunch"}], start=1
        )
    }

    def obj() -> int:
        metrics = costmod.compute_metrics(tt, grades, days, time_slots or [])
        # Adaptive penalties (work on a scaled copy)
        w = costmod.CostWeights(
            cost_blank=base_weights.cost_blank,
            cost_conflict_teacher=base_weights.cost_conflict_teacher,
            cost_conflict_class=base_weights.cost_conflict_class,
            cost_window_violation=base_weights.cost_window_violation,
            cost_adjacent_repeat=base_weights.cost_adjacent_repeat,
            cost_same_slot_repeat=base_weights.cost_same_slot_repeat,
            cost_fallback_supervised_study=base_weights.cost_fallback_supervised_study,
            cost_teacher_idle_gap=base_weights.cost_teacher_idle_gap,
            scale_adjacent_repeat=base_weights.scale_adjacent_repeat,
            scale_same_slot_repeat=base_weights.scale_same_slot_repeat,
        )
        adj_by_g = metrics.get("adjacency_by_grade", {}) or {}
        boost = 1.0
        for g, cnt in adj_by_g.items():
            if cnt and cnt >= 3:
                boost = max(boost, 1.0 + min(1.0, (cnt - 2) * 0.25))
        w.scale_adjacent_repeat = boost
        if int(metrics.get("same_slot_repeats", 0)) >= 3:
            w.scale_same_slot_repeat = 1.5
        else:
            w.scale_same_slot_repeat = 1.0
        return costmod.total_cost(metrics, w)

    def tabu_contains_move(g: str, d: str, sid: str, subj: str) -> bool:
        return (g, d, sid, subj) in tabu_cells

    def tabu_contains_swap(a1: Assignment, a2: Assignment) -> bool:
        sig = tuple(
            sorted(
                [
                    (a1.grade, a1.day, a1.slot_id, a1.subject),
                    (a2.grade, a2.day, a2.slot_id, a2.subject),
                ]
            )
        )
        return sig in tabu_swaps

    def record_move(g: str, d: str, sid: str, subj: str) -> None:
        if tabu_k > 0:
            tabu_cells.append((g, d, sid, subj))

    def record_swap(a1: Assignment, a2: Assignment) -> None:
        if tabu_k > 0:
            sig = tuple(
                sorted(
                    [
                        (a1.grade, a1.day, a1.slot_id, a1.subject),
                        (a2.grade, a2.day, a2.slot_id, a2.subject),
                    ]
                )
            )
            tabu_swaps.append(sig)

    def feasible_teacher(subj: str, g: str, d: str, sid: str) -> str | None:
        if teachers is None:
            return None
        for cand in teachers.candidates_for(subj, g) or [None]:
            if ledger.can_place(cand, g, d, sid):
                return cand
        # Allow non-teaching subjects without teacher
        if subj in {"P.E.", "UCMAS", "Extra Curricular"} and ledger.can_place(None, g, d, sid):
            return None
        return None

    def interspersed_periods_for(g: str, subj: str) -> List[str]:
        # Column interspersing: prefer periods used least by this subject
        counts: Dict[str, int] = defaultdict(int)
        for a in tt.all():
            if a.grade == g and a.subject == subj:
                counts[a.slot_id] += 1
        teaching_ids = [s["id"] for s in (time_slots or []) if s["type"] == "teaching"]
        return sorted(teaching_ids, key=lambda sid: (counts.get(sid, 0), order.get(sid, 0)))

    def ejection_chain_place(g: str, d: str, sid: str, subj: str, max_depth: int = 6) -> bool:
        # Guided ejection chain: try to place subj at (g,d,sid), eject blocker to its next best slot
        seen: Set[Tuple[str, str, str]] = set()

        def rec(target_g: str, target_d: str, target_sid: str, want_subj: str, depth: int) -> bool:
            if depth > max_depth:
                return False
            cur = tt.get(target_g, target_d, target_sid)
            if cur is None:
                # direct place
                tch = feasible_teacher(want_subj, target_g, target_d, target_sid)
                if tch is None and want_subj not in {"P.E.", "UCMAS", "Extra Curricular"}:
                    return False
                tt.place(Assignment(target_g, target_d, target_sid, want_subj, tch, False))
                ledger.place(tch, target_g, target_d, target_sid)
                return True
            if (target_g, target_d, target_sid) in seen:
                return False
            # Avoid breaking hard windows
            if (
                want_subj == "Twi"
                and target_g.startswith(("B7", "B8", "B9"))
                and target_d not in {"Wednesday", "Friday"}
            ):
                return False
            if (
                want_subj == "English"
                and target_g.startswith("B9")
                and target_d not in {"Wednesday", "Friday"}
            ):
                return False
            seen.add((target_g, target_d, target_sid))
            # try to move current blocker elsewhere
            cur_subj = cur.subject
            # Daily uniqueness constraint for the ejected subject at its new place
            candidate_sids = interspersed_periods_for(target_g, cur_subj)
            if _rng is not None:
                _rng.shuffle(candidate_sids)
            else:
                random.shuffle(candidate_sids)
            for new_sid in candidate_sids:
                if new_sid == target_sid:
                    continue
                # keep within same day first, then try other days
                for nd in [target_d] + [x for x in days if x != target_d]:
                    # Window constraints
                    if (
                        cur_subj == "Twi"
                        and target_g.startswith(("B7", "B8", "B9"))
                        and nd not in {"Wednesday", "Friday"}
                    ):
                        continue
                    if (
                        cur_subj == "English"
                        and target_g.startswith("B9")
                        and nd not in {"Wednesday", "Friday"}
                    ):
                        continue
                    if tt.get(target_g, nd, new_sid) is None and ledger.can_place(
                        cur.teacher, target_g, nd, new_sid
                    ):
                        # tentatively move cur to (nd, new_sid)
                        ledger.remove(cur.teacher, target_g, target_d, target_sid)
                        tt.remove(target_g, target_d, target_sid)
                        tt.place(Assignment(target_g, nd, new_sid, cur_subj, cur.teacher, False))
                        ledger.place(cur.teacher, target_g, nd, new_sid)
                        # try to place desired subject here
                        tch = feasible_teacher(want_subj, target_g, target_d, target_sid)
                        if tch is None and want_subj not in {"P.E.", "UCMAS", "Extra Curricular"}:
                            # revert and continue
                            ledger.remove(cur.teacher, target_g, nd, new_sid)
                            tt.remove(target_g, nd, new_sid)
                            tt.place(cur)
                            ledger.place(cur.teacher, target_g, target_d, target_sid)
                            continue
                        tt.place(Assignment(target_g, target_d, target_sid, want_subj, tch, False))
                        ledger.place(tch, target_g, target_d, target_sid)
                        return True
            return False

        return rec(g, d, sid, subj, 0)

    current_cost = obj()

    # --- Neighborhood helpers for blank_rr and kempe_period_swap ---
    RR_DEPTH = 4
    RR_NODES = 200
    RR_ATTEMPTS_PER_BLANK = 3
    KEMPE_MAX_DEPTH = 6
    KEMPE_MAX_NODES = 300

    # Effective bounds (CLI overrides when provided)
    eff_rr_depth = rr_depth if rr_depth is not None else RR_DEPTH
    eff_rr_nodes = rr_nodes if rr_nodes is not None else RR_NODES
    eff_rr_attempts = (
        rr_attempts_per_blank if rr_attempts_per_blank is not None else RR_ATTEMPTS_PER_BLANK
    )
    eff_kempe_depth = kempe_depth if kempe_depth is not None else KEMPE_MAX_DEPTH
    eff_kempe_nodes = kempe_nodes if kempe_nodes is not None else KEMPE_MAX_NODES

    teach_ids = [s["id"] for s in (time_slots or []) if s.get("type") == "teaching"]

    def _is_locked_cell(g: str, d: str, sid: str) -> bool:
        if tt.get(g, d, sid) and tt.get(g, d, sid).immutable:
            return True
        # Break/Lunch are immutable seeded in seed.py (stored as immutable)
        return False

    def _day_sequence(g: str, d: str) -> List[Assignment]:
        seq = [
            a
            for a in tt.all()
            if a.grade == g
            and a.day == d
            and a.subject not in {"Break", "Lunch", "Extra Curricular"}
        ]
        seq.sort(key=lambda x: order.get(x.slot_id, 0))
        return seq

    def _immediate_adjacency_if_place(g: str, d: str, sid: str, subj: str) -> bool:
        idx = order.get(sid, 0)
        prev = next((a for a in _day_sequence(g, d) if order.get(a.slot_id, 0) == idx - 1), None)
        nexta = next((a for a in _day_sequence(g, d) if order.get(a.slot_id, 0) == idx + 1), None)
        return (prev is not None and prev.subject == subj) or (
            nexta is not None and nexta.subject == subj
        )

    def _same_slot_repeat_count(g: str, sid: str, subj: str) -> int:
        return sum(1 for a in tt.all() if a.grade == g and a.slot_id == sid and a.subject == subj)

    def _grade_counts(g: str) -> Dict[str, int]:
        ctr: Dict[str, int] = defaultdict(int)
        for a in tt.all():
            if a.grade == g and a.subject not in {
                "Break",
                "Lunch",
                "Extra Curricular",
                "UCMAS",
                "P.E.",
            }:
                ctr[a.subject] += 1
        return ctr

    def _subject_universe_for_grade(g: str) -> List[str]:
        return [
            "English",
            "Mathematics",
            "Science",
            "Social Studies",
            "French",
            "RME",
            "Computing",
            "Creative Arts",
            "Career Tech/Pre-tech",
            "OWOP",
            "Twi",
        ]

    def _subject_windows_ok(g: str, d: str, subj: str) -> bool:
        if subj == "Twi" and g.startswith(("B7", "B8", "B9")) and d not in {"Wednesday", "Friday"}:
            return False
        if subj == "English" and g.startswith("B9") and d not in {"Wednesday", "Friday"}:
            return False
        return True

    def _teacher_for_subj_g(g: str, subj: str) -> List[str | None]:
        if teachers is None:
            return [None]
        c = teachers.candidates_for(subj, g) or [None]
        return c

    def _find_assignment_by_teacher_at(teacher: str | None, d: str, sid: str) -> Assignment | None:
        if not teacher:
            return None
        for a in tt.all():
            if a.teacher == teacher and a.day == d and a.slot_id == sid:
                return a
        return None

    def _can_place_subject_teacher(
        g: str, d: str, sid: str, subj: str, teacher: str | None
    ) -> bool:
        if not _subject_windows_ok(g, d, subj):
            return False
        if tt.get(g, d, sid) is not None:
            return False
        # No daily repetition
        if any(x.subject == subj for x in tt.all() if x.grade == g and x.day == d):
            return False
        # Teacher availability
        if not ledger.can_place(teacher, g, d, sid):
            return False
        # Weekly quotas: do not exceed maxima and prefer deficits
        if quotas is not None:
            counts = _grade_counts(g)
            maxes = quotas.maxima_for_grade(g)
            if counts.get(subj, 0) >= maxes.get(subj, 99):
                return False
        return True

    def blank_rr_once() -> bool:
        # Attempt to fix one blank via guided ejection chain; return True if improved (cost non-increasing and blank reduced)
        # Find blanks
        blanks: List[tuple[str, str, str]] = []
        for g in grades:
            for d in days:
                for sid in teach_ids:
                    if tt.get(g, d, sid) is None and not _is_locked_cell(g, d, sid):
                        blanks.append((g, d, sid))
        if not blanks:
            return False
        # Simple ordering: as-is; could sort by hardest (fewest candidates)
        g, d, sid = _rng.choice(blanks) if _rng else random.choice(blanks)
        before_metrics = costmod.compute_metrics(tt, grades, days, time_slots or [])
        before_blanks = int(before_metrics.get("blanks", 0))

        # Build candidate (subj, teacher) pairs
        universe = _subject_universe_for_grade(g)
        counts = _grade_counts(g)
        maxima = quotas.maxima_for_grade(g) if quotas else {}
        deficits: Dict[str, int] = {}
        if quotas:
            target = quotas.normalized_for_grade(g)
            for sname, tgt in target.items():
                if tgt > counts.get(sname, 0):
                    deficits[sname] = tgt - counts.get(sname, 0)

        cands: List[tuple[str, str | None]] = []
        for subj in universe:
            if counts.get(subj, 0) >= maxima.get(subj, 99):
                continue
            if not _subject_windows_ok(g, d, subj):
                continue
            for r in _teacher_for_subj_g(g, subj):
                cands.append((subj, r))
        if not cands:
            return False
        # Scoring by dispersion and deficits
        scored: List[tuple[int, str, str | None, bool]] = []  # (score, subj, teacher, causes_adj)
        for subj, r in cands:
            causes_adj = _immediate_adjacency_if_place(g, d, sid, subj)
            same_slot = _same_slot_repeat_count(g, sid, subj)
            score = 0
            # Prefer deficits
            score += 50 * deficits.get(subj, 0)
            # Prefer less same-slot
            score += max(0, 5 - same_slot) * 5
            # Penalize causing adjacency
            if causes_adj:
                score -= 30
            scored.append((score, subj, r, causes_adj))
        scored.sort(key=lambda x: x[0], reverse=True)

        attempts = 0
        for score, subj, r, causes_adj in scored:
            if attempts >= eff_rr_attempts:
                break
            attempts += 1
            # Try direct place
            if _can_place_subject_teacher(g, d, sid, subj, r):
                tt.place(Assignment(g, d, sid, subj, r, False))
                ledger.place(r, g, d, sid)
                after = costmod.compute_metrics(tt, grades, days, time_slots or [])
                if int(after.get("blanks", 0)) < before_blanks:
                    audit.append(f"blank_rr: placed directly {g} {d} {sid} -> {subj} – {r}")
                    return True
                # revert if no improvement
                ledger.remove(r, g, d, sid)
                tt.remove(g, d, sid)
            # If teacher busy, attempt chain to free that slot
            blocker = _find_assignment_by_teacher_at(r, d, sid)
            if r and blocker is not None:
                # DFS bounded
                visited: Set[tuple[str, str, str]] = set()
                nodes = [0]

                def dfs_move(a: Assignment, depth: int) -> bool:
                    if depth > eff_rr_depth or nodes[0] > eff_rr_nodes:
                        return False
                    nodes[0] += 1
                    # Candidate new positions for a (keep same teacher)
                    for nd in [a.day] + [x for x in days if x != a.day]:
                        for nsid in interspersed_periods_for(a.grade, a.subject):
                            if (a.grade, nd, nsid) in visited:
                                continue
                            if _is_locked_cell(a.grade, nd, nsid):
                                continue
                            if not _subject_windows_ok(a.grade, nd, a.subject):
                                continue
                            # Avoid daily repeat
                            if any(
                                x.subject == a.subject
                                for x in tt.all()
                                if x.grade == a.grade
                                and x.day == nd
                                and not (x.day == a.day and x.slot_id == a.slot_id)
                            ):
                                continue
                            if nd == a.day and nsid == a.slot_id:
                                continue
                            # Check teacher/class availability at target
                            if not ledger.can_place(a.teacher, a.grade, nd, nsid):
                                continue
                            occupied = tt.get(a.grade, nd, nsid)
                            # Tentatively move a
                            ledger.remove(a.teacher, a.grade, a.day, a.slot_id)
                            tt.remove(a.grade, a.day, a.slot_id)
                            if occupied is None:
                                tt.place(Assignment(a.grade, nd, nsid, a.subject, a.teacher, False))
                                ledger.place(a.teacher, a.grade, nd, nsid)
                                return True
                            else:
                                # Try to move the occupant further
                                tt.place(Assignment(a.grade, nd, nsid, a.subject, a.teacher, False))
                                ledger.place(a.teacher, a.grade, nd, nsid)
                                visited.add((a.grade, nd, nsid))
                                ok = dfs_move(occupied, depth + 1)
                                if ok:
                                    return True
                                # revert occupant move
                                visited.discard((a.grade, nd, nsid))
                                ledger.remove(a.teacher, a.grade, nd, nsid)
                                tt.remove(a.grade, nd, nsid)
                                tt.place(a)
                                ledger.place(a.teacher, a.grade, a.day, a.slot_id)
                    return False

                # Start move for blocker assignment
                if dfs_move(blocker, 0):
                    # Now place the target
                    if _can_place_subject_teacher(g, d, sid, subj, r):
                        tt.place(Assignment(g, d, sid, subj, r, False))
                        ledger.place(r, g, d, sid)
                        after = costmod.compute_metrics(tt, grades, days, time_slots or [])
                        if int(after.get("blanks", 0)) < before_blanks:
                            chain_len = 1  # lower bound (unknown exact from DFS)
                            audit.append(
                                f"blank_rr: chain placed {g} {d} {sid} -> {subj} – {r}; chain_len≈{chain_len}; blanks {before_blanks}->{int(after.get('blanks',0))}"
                            )
                            return True
                        # revert target if no improvement
                        ledger.remove(r, g, d, sid)
                        tt.remove(g, d, sid)
            # If teacher is None or not busy, skip to next candidate
        return False

    def kempe_period_swap_once() -> bool:
        # Heuristic Kempe-style: try to resolve a blank or adjacency hotspot by swapping with another period on the same day via teacher-based swap.
        # Select target
        targets: List[tuple[str, str, str]] = []
        # Prefer blanks
        for g in grades:
            for d in days:
                for sid in teach_ids:
                    if tt.get(g, d, sid) is None and not _is_locked_cell(g, d, sid):
                        targets.append((g, d, sid))
        if not targets:
            # pick an adjacency hotspot
            for g in grades:
                for d in days:
                    seq = _day_sequence(g, d)
                    for i in range(1, len(seq)):
                        if seq[i].subject == seq[i - 1].subject:
                            targets.append((g, d, seq[i].slot_id))
                            break
        if not targets:
            return False
        g, d, sid = _rng.choice(targets) if _rng else random.choice(targets)
        before = obj()
        # Candidate subject/teacher at target
        universe = _subject_universe_for_grade(g)
        cand_pairs: List[tuple[str, str | None]] = []
        for subj in universe:
            if not _subject_windows_ok(g, d, subj):
                continue
            for r in _teacher_for_subj_g(g, subj):
                cand_pairs.append((subj, r))
        # Iterate a few candidates
        scan_limit = min(len(cand_pairs), max(1, min(eff_kempe_nodes, 8)))
        for subj, r in cand_pairs[:scan_limit]:
            # Direct swap with r's current (d, t2) if any
            b = _find_assignment_by_teacher_at(r, d, sid)
            if b is None:
                # if teacher free, try simple place via swap with occupant if any
                occ = tt.get(g, d, sid)
                if occ is None and ledger.can_place(r, g, d, sid):
                    tt.place(Assignment(g, d, sid, subj, r, False))
                    ledger.place(r, g, d, sid)
                    after = obj()
                    if after <= before:
                        audit.append(f"kempe_period_swap: placed {g} {d} {sid} -> {subj} – {r}")
                        return True
                    ledger.remove(r, g, d, sid)
                    tt.remove(g, d, sid)
                continue
            # b exists: attempt swap between (g,d,sid)<->(b.grade,d,b.slot_id)
            occ = tt.get(g, d, sid)
            # Temporarily remove b and occ, then try to place subj/r at target and occ at b's slot
            ledger.remove(b.teacher, b.grade, b.day, b.slot_id)
            tt.remove(b.grade, b.day, b.slot_id)
            placed1 = False
            if ledger.can_place(r, g, d, sid) and (
                occ is None or ledger.can_place(occ.teacher, b.grade, b.day, b.slot_id)
            ):
                tt.place(Assignment(g, d, sid, subj, r, False))
                ledger.place(r, g, d, sid)
                placed1 = True
                if occ is not None:
                    # place occ to freed slot if feasible
                    if ledger.can_place(occ.teacher, b.grade, b.day, b.slot_id):
                        tt.place(
                            Assignment(b.grade, b.day, b.slot_id, occ.subject, occ.teacher, False)
                        )
                        ledger.place(occ.teacher, b.grade, b.day, b.slot_id)
                    else:
                        placed1 = False
            if placed1:
                after = obj()
                if after <= before:
                    audit.append(
                        f"kempe_period_swap: swapped {g} {d} {sid} with {b.grade} {b.day} {b.slot_id}; Δ={before-after}"
                    )
                    return True
                # revert
                if occ is not None:
                    ledger.remove(occ.teacher, b.grade, b.day, b.slot_id)
                    tt.remove(b.grade, b.day, b.slot_id)
                ledger.remove(r, g, d, sid)
                tt.remove(g, d, sid)
            # restore b
            tt.place(b)
            ledger.place(b.teacher, b.grade, b.day, b.slot_id)
        return False

    # Neighborhood loop
    iters = max_swaps
    while iters > 0:
        iters -= 1
        # Lightweight adaptive: boost adj penalty if any grade shows many adjacencies
        metrics_now = costmod.compute_metrics(tt, grades, days, time_slots or [])
        adj_by_g = metrics_now.get("adjacency_by_grade", {}) or {}
        # Adjust the base weights copy used inside obj() by tweaking the scalar on-the-fly
        if any(v >= 3 for v in adj_by_g.values()):
            base_weights.scale_adjacent_repeat = 1.5
        else:
            base_weights.scale_adjacent_repeat = 1.0

        # Prefer blank_rr early if blanks exist
        if "blank_rr" in neighborhoods:
            has_blanks = any(
                tt.get(gx, dx, sx) is None for gx in grades for dx in days for sx in teach_ids
            )
        else:
            has_blanks = False
        if has_blanks:
            choice = "blank_rr"
        else:
            choice = (
                _rng.choice(list(neighborhoods)) if _rng else random.choice(list(neighborhoods))
            )
        improved = False

        if choice == "blank_rr":
            improved = blank_rr_once()
        elif choice == "kempe_period_swap":
            improved = kempe_period_swap_once()
        elif choice == "stuck_grade":
            # pick grade with most blanks
            blanks_per_g: Dict[str, int] = defaultdict(int)
            for g in grades:
                for d in days:
                    for s in [x for x in time_slots or [] if x["type"] == "teaching"]:
                        if tt.get(g, d, s["id"]) is None:
                            blanks_per_g[g] += 1
            if blanks_per_g:
                g = max(blanks_per_g, key=blanks_per_g.get)
            else:
                g = _rng.choice(grades) if _rng else random.choice(grades)
            # Re-sample its week by fixing adjacencies and same-slot repeats
            # Find subjects with repeats in same slot
            by_subj_slot: Dict[str, Counter] = defaultdict(Counter)
            for a in tt.all():
                if a.grade == g and a.subject not in {"Break", "Lunch", "Extra Curricular"}:
                    by_subj_slot[a.subject][a.slot_id] += 1
            # try to move repeated ones to least-used periods
            for subj, ctr in by_subj_slot.items():
                rep_sids = [sid for sid, c in ctr.items() if c >= 2]
                if not rep_sids:
                    continue
                targets = interspersed_periods_for(g, subj)
                for sid in rep_sids:
                    # pick a day where subj is at sid
                    cand = [
                        a
                        for a in tt.all()
                        if a.grade == g and a.subject == subj and a.slot_id == sid
                    ]
                    if not cand:
                        continue
                    a0 = _rng.choice(cand) if _rng else random.choice(cand)
                    for tsid in targets:
                        if tsid == sid:
                            continue
                        for d in days:
                            if (
                                tt.get(g, d, tsid) is None
                                and feasible_teacher(subj, g, d, tsid) is not None
                            ):
                                before = current_cost
                                # move a0 to (d,tsid)
                                ledger.remove(a0.teacher, g, a0.day, a0.slot_id)
                                tt.remove(g, a0.day, a0.slot_id)
                                tch = feasible_teacher(subj, g, d, tsid)
                                tt.place(Assignment(g, d, tsid, subj, tch, False))
                                ledger.place(tch, g, d, tsid)
                                after = obj()
                                if after <= before:
                                    current_cost = after
                                    improved = True
                                    record_move(g, d, tsid, subj)
                                    break
                                # revert
                                ledger.remove(tch, g, d, tsid)
                                tt.remove(g, d, tsid)
                                tt.place(a0)
                                ledger.place(a0.teacher, g, a0.day, a0.slot_id)
                        if improved:
                            break
                    if improved:
                        break

        elif choice == "grade_day":
            g = _rng.choice(grades) if _rng else random.choice(grades)
            d = random.choice(days)
            # attempt to remove an adjacency by moving one of the adjacent subjects
            day_cells = sorted(
                [
                    a
                    for a in tt.all()
                    if a.grade == g
                    and a.day == d
                    and a.subject not in {"Break", "Lunch", "Extra Curricular"}
                ],
                key=lambda x: order.get(x.slot_id, 0),
            )
            moved = False
            for i in range(1, len(day_cells)):
                if day_cells[i].subject == day_cells[i - 1].subject:
                    # attempt to move the later one to a better period
                    a0 = day_cells[i]
                    for tsid in interspersed_periods_for(g, a0.subject):
                        if tsid == a0.slot_id:
                            continue
                        # try same day first via ejection chain
                        if tabu_contains_move(g, d, tsid, a0.subject):
                            continue
                        before = current_cost
                        if ejection_chain_place(g, d, tsid, a0.subject):
                            # remove old
                            ledger.remove(a0.teacher, g, a0.day, a0.slot_id)
                            tt.remove(g, a0.day, a0.slot_id)
                            after = obj()
                            if after <= before:
                                audit.append(
                                    f"Ejection chain moved {g} {d} {a0.slot_id}->{tsid} {a0.subject}"
                                )
                                current_cost = after
                                record_move(g, d, tsid, a0.subject)
                                moved = True
                                improved = True
                                break
                            # revert not easily possible (chain moved others). For safety, accept only non-worse.
                        if moved:
                            break
                if moved:
                    break

        elif choice == "grade_period":
            g = random.choice(grades)
            # pick a period id and try to disperse subjects across days
            teach_ids = [s["id"] for s in (time_slots or []) if s["type"] == "teaching"]
            if not teach_ids:
                continue
            sid = _rng.choice(teach_ids) if _rng else random.choice(teach_ids)
            # subjects occupying this sid across days
            subs = [a for a in tt.all() if a.grade == g and a.slot_id == sid]
            if len(subs) >= 2:
                # find a subject repeating too often in this slot
                ctr = Counter(a.subject for a in subs)
                bad_subj = None
                for sname, c in ctr.items():
                    if c >= 2:
                        bad_subj = sname
                        break
                if bad_subj:
                    # move one occurrence to a least-used period
                    cand = [a for a in subs if a.subject == bad_subj]
                    a0 = _rng.choice(cand) if _rng else random.choice(cand)
                    for tsid in interspersed_periods_for(g, bad_subj):
                        if tsid == sid:
                            continue
                        for d in days:
                            if tt.get(g, d, tsid) is None:
                                tch = feasible_teacher(bad_subj, g, d, tsid)
                                if tch is None and bad_subj not in {
                                    "P.E.",
                                    "UCMAS",
                                    "Extra Curricular",
                                }:
                                    continue
                                before = current_cost
                                ledger.remove(a0.teacher, g, a0.day, a0.slot_id)
                                tt.remove(g, a0.day, a0.slot_id)
                                tt.place(Assignment(g, d, tsid, bad_subj, tch, False))
                                ledger.place(tch, g, d, tsid)
                                after = obj()
                                if after <= before:
                                    current_cost = after
                                    improved = True
                                    record_move(g, d, tsid, bad_subj)
                                    break
                                # revert
                                ledger.remove(tch, g, d, tsid)
                                tt.remove(g, d, tsid)
                                tt.place(a0)
                                ledger.place(a0.teacher, g, a0.day, a0.slot_id)
                        if improved:
                            break

        if not improved:
            # Fallback to legacy pairwise improvement swaps within grade
            for g in grades:
                obj_before = obj()
                cells = [
                    a
                    for a in tt.all()
                    if a.grade == g
                    and a.subject not in {"Break", "Lunch", "Extra Curricular"}
                    and not a.immutable
                ]
                for i in range(len(cells)):
                    for j in range(i + 1, len(cells)):
                        a1 = cells[i]
                        a2 = cells[j]
                        if a1.day == a2.day and a1.slot_id == a2.slot_id:
                            continue
                        if tabu_contains_swap(a1, a2):
                            continue
                        # Daily uniqueness after swap
                        day1_subjects = {
                            x.subject
                            for x in tt.all()
                            if x.grade == g
                            and x.day == a1.day
                            and not (x.day == a1.day and x.slot_id == a1.slot_id)
                        }
                        day2_subjects = {
                            x.subject
                            for x in tt.all()
                            if x.grade == g
                            and x.day == a2.day
                            and not (x.day == a2.day and x.slot_id == a2.slot_id)
                        }
                        if a2.subject in day1_subjects or a1.subject in day2_subjects:
                            continue
                        # Window rules
                        if (
                            a2.subject == "Twi"
                            and g.startswith(("B7", "B8", "B9"))
                            and a1.day not in {"Wednesday", "Friday"}
                        ):
                            continue
                        if (
                            a1.subject == "Twi"
                            and g.startswith(("B7", "B8", "B9"))
                            and a2.day not in {"Wednesday", "Friday"}
                        ):
                            continue
                        if (
                            a2.subject == "English"
                            and g.startswith("B9")
                            and a1.day not in {"Wednesday", "Friday"}
                        ):
                            continue
                        if (
                            a1.subject == "English"
                            and g.startswith("B9")
                            and a2.day not in {"Wednesday", "Friday"}
                        ):
                            continue
                        # Teacher availability for swapped positions (allow reassignment)
                        new1_teacher = None
                        if teachers:
                            for cand in teachers.candidates_for(a2.subject, g) or [None]:
                                ledger.remove(a1.teacher, g, a1.day, a1.slot_id)
                                ok = ledger.can_place(cand, g, a1.day, a1.slot_id)
                                ledger.place(a1.teacher, g, a1.day, a1.slot_id)
                                if ok:
                                    new1_teacher = cand
                                    break
                        if new1_teacher is None and a2.subject not in {
                            "P.E.",
                            "UCMAS",
                            "Extra Curricular",
                        }:
                            continue
                        new2_teacher = None
                        if teachers:
                            for cand in teachers.candidates_for(a1.subject, g) or [None]:
                                ledger.remove(a2.teacher, g, a2.day, a2.slot_id)
                                ok = ledger.can_place(cand, g, a2.day, a2.slot_id)
                                ledger.place(a2.teacher, g, a2.day, a2.slot_id)
                                if ok:
                                    new2_teacher = cand
                                    break
                        if new2_teacher is None and a1.subject not in {
                            "P.E.",
                            "UCMAS",
                            "Extra Curricular",
                        }:
                            continue
                        # Apply tentative swap
                        ledger.remove(a1.teacher, g, a1.day, a1.slot_id)
                        ledger.remove(a2.teacher, g, a2.day, a2.slot_id)
                        tt.place(Assignment(g, a1.day, a1.slot_id, a2.subject, new1_teacher, False))
                        ledger.place(new1_teacher, g, a1.day, a1.slot_id)
                        tt.place(Assignment(g, a2.day, a2.slot_id, a1.subject, new2_teacher, False))
                        ledger.place(new2_teacher, g, a2.day, a2.slot_id)
                        obj_after = obj()
                        if obj_after <= obj_before:
                            audit.append(
                                f"Swapped {g} {a1.day} {a1.slot_id} ({a1.subject}) <-> {a2.day} {a2.slot_id} ({a2.subject})"
                            )
                            record_swap(a1, a2)
                            current_cost = obj_after
                            improved = True
                            break
                        else:
                            # revert
                            ledger.remove(new1_teacher, g, a1.day, a1.slot_id)
                            ledger.remove(new2_teacher, g, a2.day, a2.slot_id)
                            tt.place(
                                Assignment(g, a1.day, a1.slot_id, a1.subject, a1.teacher, False)
                            )
                            ledger.place(a1.teacher, g, a1.day, a1.slot_id)
                            tt.place(
                                Assignment(g, a2.day, a2.slot_id, a2.subject, a2.teacher, False)
                            )
                            ledger.place(a2.teacher, g, a2.day, a2.slot_id)
                    if improved:
                        break
                if improved:
                    break

        if current_cost == 0:
            break

    if placed == 0 and current_cost != 0:
        audit.append("No repairs applied (LNS phase may still have operated).")
    return tt, audit


def _objective(
    tt: Timetable,
    quotas: SubjectQuotas | None,
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
    penalty_same_time: int,
    penalty_adjacent: int,
    deficit_weight: int,
) -> int:
    # Legacy objective kept for compatibility where invoked internally.
    # Prefer using engine.costs for the new heuristic objective.
    deficits = 0
    if quotas is not None:
        for g in grades:
            target = quotas.normalized_for_grade(g)
            counts: Dict[str, int] = {}
            for a in tt.all():
                if a.grade == g and a.subject not in {
                    "Break",
                    "Lunch",
                    "Extra Curricular",
                    "UCMAS",
                    "P.E.",
                }:
                    counts[a.subject] = counts.get(a.subject, 0) + 1
            for subj, tgt in target.items():
                have = counts.get(subj, 0)
                if have < tgt:
                    deficits += tgt - have
    # Penalties for concurrency and adjacency across grades
    pen = 0
    # Same time penalty
    for d in days:
        for s in [t["id"] for t in time_slots if t["type"] == "teaching"]:
            subj_counts: Dict[str, int] = {}
            for a in tt.all():
                if (
                    a.day == d
                    and a.slot_id == s
                    and a.subject not in {"Break", "Lunch", "Extra Curricular"}
                ):
                    subj_counts[a.subject] = subj_counts.get(a.subject, 0) + 1
            for c in subj_counts.values():
                if c > 1:
                    pen += (c - 1) * penalty_same_time
    # Adjacency penalty (difference of 1 period across classes for same subject)
    order = {f"T{i}": i for i in range(1, 10)}
    for d in days:
        by_subj: Dict[str, List[int]] = {}
        for a in tt.all():
            if a.day == d and a.subject not in {"Break", "Lunch", "Extra Curricular"}:
                by_subj.setdefault(a.subject, []).append(order.get(a.slot_id, 0))
        for slots in by_subj.values():
            slots.sort()
            for i in range(1, len(slots)):
                if slots[i] - slots[i - 1] == 1:
                    pen += penalty_adjacent
    return deficits * deficit_weight + pen
