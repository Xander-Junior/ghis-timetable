from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path


def parse_list(arg: str) -> list[int]:
    return [int(x.strip()) for x in arg.split(",") if x.strip()]


def run_once(
    root: Path,
    seed: int,
    restarts: int,
    max_swaps: int,
    max_repairs: int,
    tabu: int,
    neighborhoods: str,
    weights_path: str | None,
) -> dict:
    cmd = [
        "python3",
        str(root / "scripts" / "run_heuristic.py"),
        "--seed",
        str(seed),
        "--restarts",
        str(restarts),
        "--max_swaps",
        str(max_swaps),
        "--max_repairs",
        str(max_repairs),
        "--tabu",
        str(tabu),
        "--neighborhoods",
        neighborhoods,
    ]
    env = os.environ.copy()
    if weights_path:
        # engine.costs reads configs/solver.toml from project root; copy if custom weights provided
        pass
    try:
        out = subprocess.check_output(cmd, cwd=str(root), stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        out = e.output
    # Parse the printed JSON metrics and path line
    stamp = None
    metrics = None
    for line in out.splitlines():
        if line.startswith("Saved best run to "):
            p = line.split("Saved best run to ", 1)[1].strip()
            if p:
                stamp = Path(p).name
        if line.startswith("{"):
            try:
                metrics = json.loads(line)
            except Exception:
                pass
    result = {
        "seed": seed,
        "restarts": restarts,
        "max_swaps": max_swaps,
        "max_repairs": max_repairs,
        "tabu": tabu,
        "neighborhoods": neighborhoods,
        "stamp": stamp,
        "metrics": metrics or {},
        "stdout": out,
    }
    # presubmit
    if stamp:
        sched = root / "outputs" / "runs" / stamp / "schedule.csv"
        try:
            subprocess.check_call(
                ["python3", str(root / "scripts" / "presubmit_check.py"), str(sched)], cwd=str(root)
            )
            result["presubmit_ok"] = True
        except subprocess.CalledProcessError as e:
            result["presubmit_ok"] = False
    else:
        result["presubmit_ok"] = False
    return result


def rank_key(res: dict) -> tuple:
    m = res.get("metrics") or {}
    blanks = m.get("blanks") or 0
    conflicts = (m.get("teacher_conflicts") or 0) + (m.get("class_conflicts") or 0)
    windows = m.get("window_violations") or 0
    fallback = m.get("fallback_supervised") or 0
    adj = m.get("adjacent_repeats_extra") or 0
    same_slot = m.get("same_slot_repeats") or 0
    penalty = m.get("penalty_sum") or 0
    return (blanks, conflicts, windows, fallback, adj, same_slot, penalty)


def main() -> int:
    ap = argparse.ArgumentParser(description="Experiment orchestrator for heuristic LNS")
    ap.add_argument("--seeds", type=str, default="42,101,202")
    ap.add_argument("--restarts", type=str, default="8")
    ap.add_argument("--max_swaps", type=str, default="2000")
    ap.add_argument("--max_repairs", type=str, default="40")
    ap.add_argument("--tabu", type=str, default="400")
    ap.add_argument(
        "--neighborhoods",
        type=str,
        default="grade_day,grade_period,stuck_grade,blank_rr,kempe_period_swap",
    )
    ap.add_argument("--weights", type=str, default="configs/solver.toml")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    seeds = parse_list(args.seeds)
    restarts_list = parse_list(args.restarts)
    max_swaps_list = parse_list(args.max_swaps)
    max_repairs_list = parse_list(args.max_repairs)
    tabu_list = parse_list(args.tabu)

    results: list[dict] = []
    for seed, restarts, max_swaps, max_repairs, tabu in itertools.product(
        seeds, restarts_list, max_swaps_list, max_repairs_list, tabu_list
    ):
        res = run_once(
            root, seed, restarts, max_swaps, max_repairs, tabu, args.neighborhoods, args.weights
        )
        results.append(res)

    # Leaderboard
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outdir = root / "outputs" / "experiments" / stamp
    outdir.mkdir(parents=True, exist_ok=True)

    # Sort
    results_sorted = sorted(results, key=rank_key)
    topN = results_sorted[: args.top]

    # Write CSV
    csv_path = outdir / "leaderboard.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "seed",
                "restarts",
                "max_swaps",
                "max_repairs",
                "tabu",
                "neighborhoods",
                "stamp",
                "blanks",
                "conflicts",
                "window_violations",
                "fallback",
                "adjacent",
                "same_slot",
                "penalty",
                "presubmit_ok",
            ]
        )
        for r in topN:
            m = r.get("metrics") or {}
            w.writerow(
                [
                    r.get("seed"),
                    r.get("restarts"),
                    r.get("max_swaps"),
                    r.get("max_repairs"),
                    r.get("tabu"),
                    r.get("neighborhoods"),
                    r.get("stamp"),
                    m.get("blanks", 0),
                    (m.get("teacher_conflicts", 0) + m.get("class_conflicts", 0)),
                    m.get("window_violations", 0),
                    m.get("fallback_supervised", 0),
                    m.get("adjacent_repeats_extra", 0),
                    m.get("same_slot_repeats", 0),
                    m.get("penalty_sum", 0),
                    r.get("presubmit_ok"),
                ]
            )

    # Write JSON
    (outdir / "leaderboard.json").write_text(json.dumps(topN, indent=2), encoding="utf-8")

    # Print top results
    print(f"Top {len(topN)} results:")
    for r in topN:
        print(
            f" - stamp={r.get('stamp')} seed={r.get('seed')} key={rank_key(r)} presubmit_ok={r.get('presubmit_ok')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
