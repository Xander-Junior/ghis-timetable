from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import sys
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from engine.solvers.cpsat import SolverConfig, solve  # type: ignore


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run CP-SAT timetable solver (segment-aware)")
    p.add_argument("--inputs", type=str, default="data/", help="Path to inputs directory")
    p.add_argument("--out", type=str, default="outputs/", help="Outputs root dir")
    p.add_argument("--timeout", type=int, default=120, help="Time limit seconds")
    p.add_argument("--workers", type=int, default=8, help="Num search workers")
    p.add_argument(
        "--config",
        type=str,
        default="configs/solver.toml",
        help="Optional TOML config (weights)",
    )
    p.add_argument(
        "--segment",
        type=str,
        choices=["JHS_B6", "P_B1_B5", "ALL"],
        default="ALL",
        help="Which segment to solve (two-stage orchestration when ALL)",
    )
    p.add_argument(
        "--segments-config",
        type=str,
        default="configs/segments.toml",
        help="Segments and bands TOML",
    )
    p.add_argument(
        "--teacher-overrides",
        type=str,
        default="configs/teacher_overrides.toml",
        help="Teacher constraints TOML",
    )
    p.add_argument(
        "--bright-kissi-budget",
        type=int,
        default=None,
        help="Optional Mr. Bright Kissi weekly assignment cap (Stage B)",
    )
    return p.parse_args()


def _load_weights(cfg: SolverConfig, cfg_path: Path) -> SolverConfig:
    try:
        import tomllib  # py311+

        if cfg_path.exists():
            with cfg_path.open("rb") as f:
                data = tomllib.load(f)
            weights = data.get("weights", {})
            cfg.weight_adjacent = int(weights.get("adjacent", cfg.weight_adjacent))
            cfg.weight_same_slot = int(weights.get("same_slot", cfg.weight_same_slot))
            cfg.weight_teacher_gaps = int(weights.get("teacher_gaps", cfg.weight_teacher_gaps))
            cfg.penalty_supervised_study = int(
                weights.get("supervised_study", cfg.penalty_supervised_study)
            )
    except Exception:
        pass
    return cfg


def _solve_stage(
    *,
    inputs: Path,
    out_root: Path,
    cfg: SolverConfig,
    segment: str,
    segments_toml: Path,
    teacher_overrides_toml: Path,
    bright_kissi_budget: int | None,
) -> Dict[str, Any]:
    # The engine solver reads data from project root; we pass segment info via kwargs
    res = solve(
        inputs,
        out_root,
        cfg,
        segment=segment,
        segments_toml=str(segments_toml),
        teacher_overrides_toml=str(teacher_overrides_toml),
        teacher_weekly_caps=(
            {"Mr. Bright Kissi": int(bright_kissi_budget)} if bright_kissi_budget is not None else None
        ),
    )
    return res


def main() -> int:
    args = parse_args()
    inputs = Path(args.inputs).resolve()
    out_root = Path(args.out).resolve()
    cfg = _load_weights(SolverConfig(timeout_sec=args.timeout, workers=args.workers), Path(args.config))

    try:
        if args.segment == "ALL":
            # Stage A: JHS_B6
            res_a = _solve_stage(
                inputs=inputs,
                out_root=out_root,
                cfg=cfg,
                segment="JHS_B6",
                segments_toml=Path(args.segments_config),
                teacher_overrides_toml=Path(args.teacher_overrides),
                bright_kissi_budget=None,
            )

            # Compute Bright Kissi usage from Stage A
            kissi_used = 0
            try:
                import csv as _csv
                with open(res_a.get("schedule_path", ""), "r", encoding="utf-8") as f:
                    reader = _csv.DictReader(f)
                    for r in reader:
                        if r.get("Teacher") == "Mr. Bright Kissi" and r.get("Subject"):
                            kissi_used += 1
            except Exception:
                pass

            # Stage B: P_B1_B5 with optional budget cap if provided
            res_b = _solve_stage(
                inputs=inputs,
                out_root=out_root,
                cfg=cfg,
                segment="P_B1_B5",
                segments_toml=Path(args.segments_config),
                teacher_overrides_toml=Path(args.teacher_overrides),
                bright_kissi_budget=args.bright_kissi_budget,
            )
            # Emit pointers to both runs
            print(json.dumps({"stage_a": res_a, "stage_b": res_b}, indent=2))
            s_ok = lambda r: str(r.get("status", "")).upper() in {"OPTIMAL", "FEASIBLE"}
            return 0 if s_ok(res_a) and s_ok(res_b) else 1

        else:
            res = _solve_stage(
                inputs=inputs,
                out_root=out_root,
                cfg=cfg,
                segment=args.segment,
                segments_toml=Path(args.segments_config),
                teacher_overrides_toml=Path(args.teacher_overrides),
                bright_kissi_budget=args.bright_kissi_budget,
            )
            print(json.dumps({k: v for k, v in res.items() if k != "metrics"}, indent=2))
            status = str(res.get("status", "")).upper()
            return 0 if status in {"OPTIMAL", "FEASIBLE"} else 1
    except RuntimeError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
