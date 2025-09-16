from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore

from .models.timetable import Timetable


@dataclass
class CostWeights:
    # Baselines; treat VERY_HIGH >> HIGH >> MEDIUM >> LOW
    cost_blank: int = 1_000_000
    cost_conflict_teacher: int = 1_000_000
    cost_conflict_class: int = 1_000_000
    cost_window_violation: int = 1_000_000
    cost_adjacent_repeat: int = 2_500
    cost_same_slot_repeat: int = 800
    cost_fallback_supervised_study: int = 250_000
    cost_teacher_idle_gap: int = 200

    # Dynamic scalars for adaptive penalties (>=1.0)
    scale_adjacent_repeat: float = 1.0
    scale_same_slot_repeat: float = 1.0


def _project_root(default: bool = True) -> Path:
    # engine/costs.py -> project root is parents[1]
    here = Path(__file__).resolve()
    return here.parents[1]


def load_weights(project_root: Path | str | None = None) -> CostWeights:
    """Load cost weights from configs/solver.toml if present, else defaults.

    Expected keys (top-level or under [weights]):
      - cost_blank, cost_conflict, cost_window_violation, cost_fallback,
        cost_adjacent_repeat, cost_same_slot_repeat, cost_teacher_idle_gap
    cost_conflict maps to both teacher and class conflicts.
    """
    base = CostWeights()
    root: Path = _project_root() if project_root is None else Path(project_root)
    cfg = root / "configs" / "solver.toml"
    if tomllib is None or not cfg.exists():
        return base
    try:
        data: Dict[str, Any] = tomllib.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return base
    # Support either top-level or [weights]
    w = data.get("weights") if isinstance(data.get("weights"), dict) else data

    def get_int(name: str, default: int) -> int:
        try:
            v = int(w.get(name, default))  # type: ignore[arg-type]
            return v
        except Exception:
            return default

    conflict = get_int("cost_conflict", base.cost_conflict_class)
    loaded = CostWeights(
        cost_blank=get_int("cost_blank", base.cost_blank),
        cost_conflict_teacher=conflict,
        cost_conflict_class=conflict,
        cost_window_violation=get_int("cost_window_violation", base.cost_window_violation),
        cost_adjacent_repeat=get_int("cost_adjacent_repeat", base.cost_adjacent_repeat),
        cost_same_slot_repeat=get_int("cost_same_slot_repeat", base.cost_same_slot_repeat),
        cost_fallback_supervised_study=get_int(
            "cost_fallback", base.cost_fallback_supervised_study
        ),
        cost_teacher_idle_gap=get_int("cost_teacher_idle_gap", base.cost_teacher_idle_gap),
        scale_adjacent_repeat=1.0,
        scale_same_slot_repeat=1.0,
    )
    return loaded


def _slot_order_map(time_slots: List[dict]) -> Dict[str, int]:
    order: Dict[str, int] = {}
    for idx, s in enumerate(
        [t for t in time_slots if t["type"] in {"teaching", "break", "lunch"}], start=1
    ):
        order[s["id"]] = idx
    return order


def compute_metrics(
    tt: Timetable,
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
) -> Dict[str, object]:
    # blanks
    teaching_slots = [s for s in time_slots if s["type"] == "teaching"]
    blanks = 0
    for g in grades:
        for d in days:
            for s in teaching_slots:
                if tt.get(g, d, s["id"]) is None:
                    blanks += 1

    # conflicts
    t_busy = Counter()
    c_busy = Counter()
    for a in tt.all():
        c_busy[(a.grade, a.day, a.slot_id)] += 1
        if a.teacher:
            t_busy[(a.teacher, a.day, a.slot_id)] += 1
    teacher_conflicts = sum(1 for _, v in t_busy.items() if v > 1)
    class_conflicts = sum(1 for _, v in c_busy.items() if v > 1)

    # window violations
    window_violations = 0
    for a in tt.all():
        if a.subject == "Twi" and (
            a.grade.startswith("B7") or a.grade.startswith("B8") or a.grade.startswith("B9")
        ):
            if a.day not in {"Wednesday", "Friday"}:
                window_violations += 1
        if a.subject == "English" and a.grade.startswith("B9"):
            if a.day not in {"Wednesday", "Friday"}:
                window_violations += 1

    # enforce seed: B9 Friday T9 must be English; Extra Curricular forbidden there
    b9_fri_t9_violation = 0
    for g in grades:
        if not g.startswith("B9"):
            continue
        a = tt.get(g, "Friday", "T9")
        if a is None or a.subject != "English":
            b9_fri_t9_violation += 1

    # adjacency (same subject back-to-back in a day) per grade/day
    order = _slot_order_map(time_slots)
    adjacency_by_grade: Dict[str, int] = defaultdict(int)
    adjacency_positions: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    for g in grades:
        for d in days:
            # build ordered slots for the day
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
            for i in range(1, len(day_cells)):
                if day_cells[i].subject == day_cells[i - 1].subject:
                    adjacency_by_grade[g] += 1
                    adjacency_positions[g].append(
                        (d, day_cells[i - 1].slot_id, day_cells[i].slot_id)
                    )

    # Special allowance: For B9 English, allow exactly one double-block per week with zero cost
    b9_english_double_ok: Dict[str, int] = defaultdict(int)  # grade -> used free allowance (0/1)
    extra_adjacencies_count = 0
    for g, count in adjacency_by_grade.items():
        if g.startswith("B9") and count > 0:
            # Count actual English adjacencies to potentially discount one
            eng_adj = 0
            for d in days:
                cells = sorted(
                    [a for a in tt.all() if a.grade == g and a.day == d and a.subject == "English"],
                    key=lambda x: order.get(x.slot_id, 0),
                )
                for i in range(1, len(cells)):
                    if order.get(cells[i].slot_id, 0) - order.get(cells[i - 1].slot_id, 0) == 1:
                        eng_adj += 1
            free = min(1, eng_adj)
            b9_english_double_ok[g] = free
            extra_adjacencies_count += max(0, count - free)
        else:
            extra_adjacencies_count += count

    # same slot repeat across days per grade/subject
    same_slot_repeat = 0
    for g in grades:
        by_subj: Dict[str, Counter] = defaultdict(Counter)
        for a in tt.all():
            if a.grade == g and a.subject not in {"Break", "Lunch", "Extra Curricular"}:
                by_subj[a.subject][a.slot_id] += 1
        for subj, ctr in by_subj.items():
            for _, v in ctr.items():
                if v >= 2:
                    same_slot_repeat += v - 1

    # fallback supervised study (if present)
    fallback_supervised = sum(1 for a in tt.all() if a.subject == "Supervised Study")

    # teacher idle gaps: count per-day gaps for each teacher
    teacher_idle_gaps = 0
    teacher_days: Dict[Tuple[str, str], List[int]] = defaultdict(
        list
    )  # (teacher, day) -> slot indexes
    for a in tt.all():
        if a.teacher:
            teacher_days[(a.teacher, a.day)].append(order.get(a.slot_id, 0))
    for (_, _), idxs in teacher_days.items():
        if len(idxs) <= 1:
            continue
        idxs.sort()
        # count the number of gaps of length >=1 between consecutive assignments (i.e., idle windows)
        for i in range(1, len(idxs)):
            gap = idxs[i] - idxs[i - 1]
            if gap > 1:
                teacher_idle_gaps += gap - 1  # count empty periods as gaps

    return {
        "blanks": blanks,
        "teacher_conflicts": teacher_conflicts,
        "class_conflicts": class_conflicts,
        "window_violations": window_violations + b9_fri_t9_violation,
        "adjacent_repeats_extra": extra_adjacencies_count,  # already accounts for B9 free double
        "same_slot_repeats": same_slot_repeat,
        "fallback_supervised": fallback_supervised,
        "teacher_idle_gaps": teacher_idle_gaps,
        "adjacency_by_grade": dict(adjacency_by_grade),
    }


def total_cost(metrics: Dict[str, object], w: CostWeights) -> int:
    # Very high priority hard violations
    cost = 0
    cost += int(metrics.get("blanks", 0)) * w.cost_blank
    cost += int(metrics.get("teacher_conflicts", 0)) * w.cost_conflict_teacher
    cost += int(metrics.get("class_conflicts", 0)) * w.cost_conflict_class
    cost += int(metrics.get("window_violations", 0)) * w.cost_window_violation

    # Soft costs
    adj = int(metrics.get("adjacent_repeats_extra", 0))
    ssr = int(metrics.get("same_slot_repeats", 0))
    cost += int(round(adj * w.cost_adjacent_repeat * w.scale_adjacent_repeat))
    cost += int(round(ssr * w.cost_same_slot_repeat * w.scale_same_slot_repeat))

    cost += int(metrics.get("fallback_supervised", 0)) * w.cost_fallback_supervised_study
    cost += int(metrics.get("teacher_idle_gaps", 0)) * w.cost_teacher_idle_gap
    return cost
