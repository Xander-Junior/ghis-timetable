from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root on sys.path
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from engine import costs as costmod
from engine.data.loader import load_data
from engine.data.registry import ConstraintRegistry, OccupancyLedger, SubjectQuotas
from engine.data.teachers import TeacherDirectory
from engine.models.timetable import Timetable
from engine.render.csv_out import csv_blocks
from engine.scheduler.fill import fill_schedule
from engine.scheduler.repair import repair_schedule
from engine.scheduler.seed import seed_schedule
from engine.validate.checks import validate_all


def build_once(
    *,
    max_repairs: int,
    max_swaps: int,
    tabu: int,
    neighborhoods: list[str],
    relax_electives: object = False,
    seed_offset: int = 0,
    base_seed: int = 12345,
    rr_depth: int | None = None,
    rr_nodes: int | None = None,
    rr_attempts_per_blank: int | None = None,
    kempe_depth: int | None = None,
    kempe_nodes: int | None = None,
):
    # Load data
    loaded = load_data(root)
    structure = loaded.structure
    grades = structure["grades"]
    days = structure["days"]
    time_slots = structure["time_slots"]
    constraints = loaded.constraints
    quotas = SubjectQuotas(constraints.get("weekly_quotas", {}))
    if relax_electives:
        quotas.set_relax_electives(relax_electives)

    # Randomize to diversify restarts
    rnd = random.Random(base_seed + seed_offset)
    grades = grades[:]
    days = days[:]
    rnd.shuffle(grades)
    rnd.shuffle(days)

    ledger = OccupancyLedger()
    tt = Timetable()
    teacher_dir = TeacherDirectory(loaded.teachers)
    seed_tt, seed_audit = seed_schedule(
        tt, ledger, grades, days, time_slots, constraints, teacher_dir
    )
    tt, fill_audit = fill_schedule(seed_tt, ledger, grades, days, time_slots, quotas, teacher_dir)
    all_audit: list[str] = []
    all_audit.extend(["Seed:"] + seed_audit + [""] + ["Fill:"] + fill_audit)
    for _ in range(max_repairs):
        tt, repair_audit = repair_schedule(
            tt,
            ledger,
            grades,
            days,
            teacher_dir,
            quotas,
            max_swaps=max_swaps,
            time_slots=time_slots,
            neighborhoods=neighborhoods,
            tabu_k=tabu,
            rng=rnd,
            weights=costmod.load_weights(root),
            rr_depth=rr_depth,
            rr_nodes=rr_nodes,
            rr_attempts_per_blank=rr_attempts_per_blank,
            kempe_depth=kempe_depth,
            kempe_nodes=kempe_nodes,
        )
        all_audit.extend([""] + repair_audit)

    # Metrics
    metrics = costmod.compute_metrics(tt, grades, days, time_slots)
    weights = costmod.CostWeights()
    penalty_sum = costmod.total_cost(metrics, weights)
    metrics_out = {
        **metrics,
        "penalty_sum": penalty_sum,
    }
    validation = validate_all(tt, grades, days, time_slots, quotas.base)
    return tt, grades, days, time_slots, metrics_out, all_audit, validation


def lex_key(metrics: dict) -> tuple:
    blanks = int(metrics.get("blanks", 0))
    conflicts = int(metrics.get("teacher_conflicts", 0)) + int(metrics.get("class_conflicts", 0))
    windows = int(metrics.get("window_violations", 0))
    penalty = int(metrics.get("penalty_sum", 0))
    return (blanks, conflicts, windows, penalty)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run heuristic LNS timetable search")
    ap.add_argument("--max_repairs", type=int, default=40)
    ap.add_argument("--max_swaps", type=int, default=2000)
    ap.add_argument("--restarts", type=int, default=8)
    ap.add_argument("--tabu", type=int, default=400)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument(
        "--rr-depth",
        dest="rr_depth",
        type=int,
        default=None,
        help="Override blank_rr DFS depth (default 4)",
    )
    ap.add_argument(
        "--rr-nodes",
        dest="rr_nodes",
        type=int,
        default=None,
        help="Override blank_rr node cap per attempt (default 200)",
    )
    ap.add_argument(
        "--rr-attempts-per-blank",
        dest="rr_attempts_per_blank",
        type=int,
        default=None,
        help="Override blank_rr attempts per blank (default 3)",
    )
    ap.add_argument(
        "--kempe-depth",
        dest="kempe_depth",
        type=int,
        default=None,
        help="Override kempe max chain depth (default 6)",
    )
    ap.add_argument(
        "--kempe-nodes",
        dest="kempe_nodes",
        type=int,
        default=None,
        help="Override kempe node/scan cap (default 300)",
    )
    ap.add_argument(
        "--neighborhoods",
        type=str,
        default="grade_day,grade_period,stuck_grade",
        help="Neighborhoods: grade_day,grade_period,teacher_scope,stuck_grade",
    )
    args = ap.parse_args()
    neighborhoods = [s.strip() for s in args.neighborhoods.split(",") if s.strip()]

    best = None
    best_pack = None
    best_validation = None
    best_audit = None
    # Pass A: normal quotas
    for r in range(args.restarts):
        tt, grades, days, time_slots, metrics, audit, val = build_once(
            max_repairs=args.max_repairs,
            max_swaps=args.max_swaps,
            tabu=args.tabu,
            neighborhoods=neighborhoods,
            relax_electives=False,
            seed_offset=r,
            base_seed=args.seed,
            rr_depth=args.rr_depth,
            rr_nodes=args.rr_nodes,
            rr_attempts_per_blank=args.rr_attempts_per_blank,
            kempe_depth=args.kempe_depth,
            kempe_nodes=args.kempe_nodes,
        )
        key = lex_key(metrics)
        if best is None or key < best:
            best = key
            best_pack = (tt, grades, days, time_slots, metrics)
            best_validation = val
            best_audit = audit

    # If blanks remain, apply targeted -1 relaxation for non-cores for failing grades and re-attempt once
    tt, grades, days, time_slots, metrics = best_pack  # type: ignore
    blanks_by_grade: dict[str, int] = {}
    teaching_slots = [s for s in time_slots if s["type"] == "teaching"]
    for g in grades:
        c = 0
        for d in days:
            for s in teaching_slots:
                if tt.get(g, d, s["id"]) is None:
                    c += 1
        if c:
            blanks_by_grade[g] = c
    if blanks_by_grade:
        tt2, grades2, days2, time_slots2, metrics2, audit2, val2 = build_once(
            max_repairs=args.max_repairs,
            max_swaps=args.max_swaps,
            tabu=args.tabu,
            neighborhoods=neighborhoods,
            relax_electives=sorted(blanks_by_grade.keys()),
            seed_offset=9999,
            base_seed=args.seed,
            rr_depth=args.rr_depth,
            rr_nodes=args.rr_nodes,
            rr_attempts_per_blank=args.rr_attempts_per_blank,
            kempe_depth=args.kempe_depth,
            kempe_nodes=args.kempe_nodes,
        )
        key2 = lex_key(metrics2)
        if key2 < best:
            best = key2
            best_pack = (tt2, grades2, days2, time_slots2, metrics2)
            best_validation = val2
            best_audit = audit2

    # Save best to outputs/runs/<stamp>/
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = root / "outputs" / "runs" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    tt, grades, days, time_slots, metrics = best_pack  # type: ignore
    csv_text = csv_blocks(tt, grades, days, time_slots)
    (outdir / "schedule.csv").write_text(csv_text, encoding="utf-8")
    (outdir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (outdir / "audit.log").write_text("\n".join(best_audit or []), encoding="utf-8")
    (outdir / "validation.json").write_text(
        json.dumps(best_validation or {}, indent=2), encoding="utf-8"
    )
    print(f"Saved best run to {outdir}")
    print(json.dumps({"lex_key": best, **metrics}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
