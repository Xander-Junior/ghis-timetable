from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from engine.solvers.cpsat import SolverConfig, solve


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run CP-SAT timetable solver wrapper")
    p.add_argument("--inputs", type=str, default="data/", help="Path to inputs directory")
    p.add_argument("--out", type=str, default="outputs/", help="Outputs root dir")
    p.add_argument("--timeout", type=int, default=120, help="Time limit seconds")
    p.add_argument("--workers", type=int, default=8, help="Num search workers")
    p.add_argument("--config", type=str, default="configs/solver.toml", help="Optional TOML config (weights)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    inputs = Path(args.inputs).resolve()
    out_root = Path(args.out).resolve()
    cfg = SolverConfig(timeout_sec=args.timeout, workers=args.workers)

    # Optional: read TOML weights if present; fallback to defaults
    try:
        import tomllib  # py311+

        cfg_path = Path(args.config)
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

    try:
        res = solve(inputs, out_root, cfg)
    except RuntimeError as e:
        # Likely OR-Tools not installed
        print(f"ERROR: {e}")
        return 1
    print(json.dumps({k: v for k, v in res.items() if k != "metrics"}, indent=2))
    status = str(res.get("status", "")).upper()
    if status not in {"OPTIMAL", "FEASIBLE"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
