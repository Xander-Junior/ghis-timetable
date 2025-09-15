from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

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
        "--segments",
        type=str,
        default=None,
        help="Segment multi-solve mode: 'auto', 'config', or comma-separated list of segment ids",
    )
    p.add_argument(
        "--segments-config",
        type=str,
        default="configs/segments.toml",
        help="Segments and bands TOML",
    )
    p.add_argument(
        "--global-exceptions",
        type=str,
        default="configs/segments.toml",
        help="Global exceptions source (cross-segment teachers)",
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


def _append_audit_line(run_dir: str | Path, line: str) -> None:
    try:
        p = Path(run_dir) / "audit.log"
        with p.open("a", encoding="utf-8") as f:
            f.write("\n" + line.strip())
    except Exception:
        pass


def _infer_teacher_total_cap(teacher_name: str) -> int:
    """Infer teacher's total Computing capacity across all grades by quotas.
    We approximate by summing the weekly Computing quota for every grade the teacher can teach.
    """
    try:
        from engine.data.loader import load_data  # type: ignore
    except Exception:
        return 0
    root = Path(__file__).resolve().parents[1]
    loaded = load_data(root)
    weekly = loaded.constraints.get("weekly_quotas", {})
    comp_q = int(weekly.get("Computing", 0))
    if comp_q <= 0:
        return 0

    def _grade_base(g: str) -> str:
        for i, ch in enumerate(g):
            if ch.isalpha() and i > 0 and g[i - 1].isdigit():
                return g[:i]
        return g

    teachers = loaded.teachers.get("teachers", [])
    tinfo = next((t for t in teachers if t.get("name") == teacher_name), None)
    if not tinfo:
        return 0
    allowed_grades = set(tinfo.get("grades", []) or [])
    total = 0
    for g in loaded.structure.get("grades", []):
        gb = _grade_base(g)
        if gb in allowed_grades:
            total += comp_q
    return total


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
            {"Mr. Bright Kissi": int(bright_kissi_budget)}
            if bright_kissi_budget is not None
            else None
        ),
    )
    return res


def main() -> int:
    args = parse_args()
    inputs = Path(args.inputs).resolve()
    out_root = Path(args.out).resolve()
    cfg = _load_weights(
        SolverConfig(timeout_sec=args.timeout, workers=args.workers), Path(args.config)
    )

    try:
        # Prefer multi-segment flow when --segments is provided
        if args.segments:
            import time, json as _json, shutil
            from datetime import datetime
            stamp = time.strftime("%Y%m%d_%H%M%S")
            run_root = out_root / "runs" / stamp
            run_root.mkdir(parents=True, exist_ok=True)

            # Determine segments mapping and exception teachers
            segments_map: dict[str, list[str]] = {}
            exceptions: list[str] = []
            if args.segments.strip().lower() == "auto":
                # Expect outputs/segments.json from detector
                seg_json = out_root / "segments.json"
                if not seg_json.exists():
                    # fallback: run detector
                    from scripts.segment_detect import main as _det  # type: ignore

                    _det()
                if seg_json.exists():
                    data = _json.loads(seg_json.read_text(encoding="utf-8"))
                    segments_map = data.get("segments", {}) or {}
                    exceptions = list(data.get("cross_segment_teachers", []) or [])
            elif args.segments.strip().lower() == "config":
                try:
                    import tomllib

                    with Path(args.segments_config).open("rb") as f:
                        t = tomllib.load(f)
                    mapping = t.get("segments", {}) or {}
                    rev: dict[str, list[str]] = {}
                    for g, seg in mapping.items():
                        rev.setdefault(str(seg), []).append(str(g))
                    segments_map = rev
                    exceptions = list((t.get("cross_segment_teachers", {}) or {}).get("names", []) or [])
                except Exception:
                    segments_map = {}
            else:
                # LIST of segment ids; rely on config mapping
                req = {s.strip() for s in args.segments.split(",") if s.strip()}
                try:
                    import tomllib

                    with Path(args.segments_config).open("rb") as f:
                        t = tomllib.load(f)
                    mapping = t.get("segments", {}) or {}
                    rev: dict[str, list[str]] = {}
                    for g, seg in mapping.items():
                        if str(seg) in req:
                            rev.setdefault(str(seg), []).append(str(g))
                    segments_map = rev
                    exceptions = list((t.get("cross_segment_teachers", {}) or {}).get("names", []) or [])
                except Exception:
                    segments_map = {}
            if not segments_map:
                print("ERROR: No segments determined.")
                return 1

            # Optionally prepare a temporary segments TOML if using auto (so solver can filter)
            segs_toml_path = Path(args.segments_config)
            if args.segments.strip().lower() == "auto":
                segs_toml_path = run_root / "segments.auto.toml"
                with segs_toml_path.open("w", encoding="utf-8") as f:
                    f.write("[segments]\n")
                    for seg, glist in segments_map.items():
                        for g in glist:
                            f.write(f"{g}='{seg}'\n")
                    if exceptions:
                        f.write("\n[cross_segment_teachers]\n")
                        f.write("names=[" + ",".join(f"'{n}'" for n in exceptions) + "]\n")

            # Solve each segment and relocate artifacts under run_root/<segment>
            per_seg_dirs: dict[str, Path] = {}
            meta_lines: list[str] = []
            for seg in sorted(segments_map.keys()):
                res = _solve_stage(
                    inputs=inputs,
                    out_root=out_root,
                    cfg=cfg,
                    segment=seg,
                    segments_toml=segs_toml_path,
                    teacher_overrides_toml=Path(args.teacher_overrides),
                    bright_kissi_budget=None,
                )
                status = str(res.get("status", "")).upper()
                if status not in {"OPTIMAL", "FEASIBLE"}:
                    print(_json.dumps({"segment": seg, **{k: v for k, v in res.items() if k != "metrics"}}, indent=2))
                    return 1
                seg_dir = run_root / seg
                seg_dir.mkdir(parents=True, exist_ok=True)
                # Move/copy artifacts
                for key in ("schedule_path", "metrics_path", "audit_path"):
                    p = Path(res.get(key, ""))
                    if p.exists():
                        target = seg_dir / p.name
                        shutil.copy2(p, target)
                # Simple per-segment presubmit is handled by Makefile later
                per_seg_dirs[seg] = seg_dir
                # Compute exception usage
                used_counts: dict[str, int] = {}
                try:
                    import csv as _csv

                    with (seg_dir / "schedule.csv").open("r", encoding="utf-8") as f:
                        r = _csv.DictReader(f)
                        for row in r:
                            tname = row.get("Teacher", "").strip()
                            if tname in exceptions:
                                used_counts[tname] = used_counts.get(tname, 0) + 1
                except Exception:
                    pass
                for name, cnt in used_counts.items():
                    meta_lines.append(f"{seg}: {name} used={cnt}")

            # Merged schedule and audit
            merged_dir = run_root / "merged"
            merged_dir.mkdir(parents=True, exist_ok=True)
            # Concatenate CSVs
            import csv as _csv

            header = ["Grade", "Day", "PeriodStart", "PeriodEnd", "Subject", "Teacher"]
            with (merged_dir / "schedule.csv").open("w", encoding="utf-8", newline="") as f_out:
                w = _csv.writer(f_out)
                w.writerow(header)
                for seg in sorted(per_seg_dirs.keys()):
                    with (per_seg_dirs[seg] / "schedule.csv").open("r", encoding="utf-8") as f_in:
                        r = _csv.reader(f_in)
                        # skip header
                        first = True
                        for row in r:
                            if first:
                                first = False
                                continue
                            w.writerow(row)
            # Merged audit
            with (merged_dir / "audit.log").open("w", encoding="utf-8") as f:
                f.write("Segment solves completed\n")
                for line in meta_lines:
                    f.write(line + "\n")

            # Point latest symlink to new stamp
            latest_link = out_root / "runs" / "latest"
            try:
                latest_link.symlink_to(stamp)
            except FileExistsError:
                latest_link.unlink(missing_ok=True)
                latest_link.symlink_to(stamp)
            print(_json.dumps({"stamp": stamp, "segments": sorted(per_seg_dirs.keys())}, indent=2))
            return 0
        elif args.segment == "ALL":
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

            # Compute Bright Kissi Computing usage from Stage A
            kissi_used = 0
            try:
                import csv as _csv

                with open(res_a.get("schedule_path", ""), "r", encoding="utf-8") as f:
                    reader = _csv.DictReader(f)
                    for r in reader:
                        if (
                            r.get("Teacher") == "Mr. Bright Kissi"
                            and r.get("Subject") == "Computing"
                        ):
                            kissi_used += 1
            except Exception:
                pass
            if res_a.get("run_dir"):
                _append_audit_line(res_a["run_dir"], f"kissi_used_stageA={kissi_used}")

            # Determine Stage B cap: user override or inferred remaining
            budget = args.bright_kissi_budget
            source = "override" if budget is not None else "auto"
            if budget is None:
                total_cap = _infer_teacher_total_cap("Mr. Bright Kissi")
                remaining = total_cap - kissi_used
                budget = max(0, remaining)
                if remaining < 0:
                    print(
                        f"ERROR: Bright Kissi budget negative after Stage A (remaining={remaining})."
                    )
                    return 1

            # Stage B: P_B1_B5 with optional budget cap if provided
            res_b = _solve_stage(
                inputs=inputs,
                out_root=out_root,
                cfg=cfg,
                segment="P_B1_B5",
                segments_toml=Path(args.segments_config),
                teacher_overrides_toml=Path(args.teacher_overrides),
                bright_kissi_budget=budget,
            )
            if res_b.get("run_dir"):
                _append_audit_line(res_b["run_dir"], f"kissi_budget_cap={budget} source={source}")
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
