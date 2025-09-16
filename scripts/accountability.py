from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

HEADER = ["Grade", "Day", "PeriodStart", "PeriodEnd", "Subject", "Teacher"]


def read_csv_rows(p: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with p.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append({k: row.get(k, "").strip() for k in HEADER})
    return rows


def load_structure(root: Path) -> Dict[str, Any]:
    with (root / "data" / "structure.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def compute_metrics(rows: List[Dict[str, str]], structure: Dict[str, Any]) -> Dict[str, Any]:
    # Reuse solver's metric computation for consistency
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from engine.solvers.cpsat import _compute_metrics as _cm  # type: ignore

    return _cm(rows, structure)


def load_segments(root: Path) -> Tuple[Dict[str, str], List[str]]:
    segs: Dict[str, str] = {}
    exceptions: List[str] = []
    try:
        import tomllib

        with (root / "configs" / "segments.toml").open("rb") as f:
            t = tomllib.load(f)
        segs = {k: str(v) for k, v in (t.get("segments", {}) or {}).items()}
        exceptions = list((t.get("cross_segment_teachers", {}) or {}).get("names", []))
    except Exception:
        pass
    return segs, exceptions


def seg_of(grade: str, segs: Dict[str, str]) -> str:
    # Normalize B7A -> B7
    gb = grade
    for i, ch in enumerate(grade):
        if ch.isalpha() and i > 0 and grade[i - 1].isdigit():
            gb = grade[:i]
            break
    return segs.get(gb, "")


def cross_segment_conflicts(
    rows: List[Dict[str, str]], segs: Dict[str, str], only_names: List[str]
) -> int:
    # Detect if a teacher appears in two segments at the same day/start concurrently
    # Count conflicts for names in only_names
    from collections import defaultdict

    slotmap: Dict[Tuple[str, str, str], set[str]] = defaultdict(set)
    for r in rows:
        if not r["Teacher"] or r["Subject"] in {"Break", "Lunch"}:
            continue
        s = seg_of(r["Grade"], segs)
        if r["Teacher"] in set(only_names):
            key = (r["Teacher"], r["Day"], r["PeriodStart"])  # Teacher/day/start
            slotmap[key].add(s or "")
    # Conflict if more than one distinct segment for the key
    return sum(1 for segset in slotmap.values() if len(segset) > 1)


def infer_total_cap(name: str) -> int:
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from scripts.run_cpsat import _infer_teacher_total_cap  # type: ignore

        return int(_infer_teacher_total_cap(name))
    except Exception:
        return 0


def compute_usage(rows: List[Dict[str, str]], name: str, subject: str) -> int:
    return sum(1 for r in rows if r["Teacher"] == name and r["Subject"] == subject)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run retrospective + accountability")
    ap.add_argument("previous", type=Path, help="Path to previous run dir")
    ap.add_argument("latest", type=Path, help="Path to latest run dir")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    structure = load_structure(root)

    def load_run(dirp: Path) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        sched = dirp / "schedule.csv"
        rows = read_csv_rows(sched) if sched.exists() else []
        metrics_path = dirp / "metrics.json"
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            except Exception:
                metrics = {}
        else:
            metrics = {}
        # If metrics missing core fields, compute fallback metrics
        for k in [
            "blanks",
            "teacher_conflicts",
            "class_conflicts",
            "window_violations",
            "adjacency_violations",
            "same_slot_repeat_score",
            "fallback_usage",
        ]:
            if k not in metrics:
                cm = compute_metrics(rows, structure)
                metrics.update({kk: cm.get(kk, 0) for kk in cm.keys()})
                break
        return rows, metrics

    rows_prev, m_prev = load_run(args.previous)
    rows_latest, m_latest = load_run(args.latest)

    segs, exceptions = load_segments(root)
    xseg_conf = cross_segment_conflicts(rows_latest, segs, exceptions)

    # Weekly cap info for Bright Kissi (Computing)
    name = "Mr. Bright Kissi"
    total_cap = infer_total_cap(name)
    used = compute_usage(rows_latest, name, "Computing")
    remaining = max(0, total_cap - used)

    # Per-segment metrics (if present)
    def scan_segments(root_dir: Path) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for d in sorted([p for p in root_dir.iterdir() if p.is_dir()]):
            if d.name == "merged":
                continue
            sched = d / "schedule.csv"
            if not sched.exists():
                continue
            rows = read_csv_rows(sched)
            metrics_path = d / "metrics.json"
            metrics = {}
            if metrics_path.exists():
                try:
                    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                except Exception:
                    metrics = {}
            # Fill if missing
            for k in [
                "blanks",
                "teacher_conflicts",
                "class_conflicts",
                "window_violations",
                "adjacency_violations",
                "same_slot_repeat_score",
                "fallback_usage",
            ]:
                if k not in metrics:
                    cm = compute_metrics(rows, structure)
                    metrics.update({kk: cm.get(kk, 0) for kk in cm.keys()})
                    break
            out[d.name] = metrics
        return out

    seg_prev = scan_segments(args.previous)
    seg_latest = scan_segments(args.latest)

    # Load day choices if present
    def load_day_choices(dirp: Path) -> Dict[str, Any]:
        p = dirp / "day_choices.json"
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    dc_prev = load_day_choices(args.previous)
    dc_latest = load_day_choices(args.latest)

    retrospective = {
        "previous": str(args.previous),
        "latest": str(args.latest),
        "metrics_prev": {
            k: m_prev.get(k, 0)
            for k in [
                "blanks",
                "teacher_conflicts",
                "class_conflicts",
                "window_violations",
                "adjacency_violations",
                "same_slot_repeat_score",
                "fallback_usage",
            ]
        },
        "metrics_latest": {
            k: m_latest.get(k, 0)
            for k in [
                "blanks",
                "teacher_conflicts",
                "class_conflicts",
                "window_violations",
                "adjacency_violations",
                "same_slot_repeat_score",
                "fallback_usage",
            ]
        },
        "segments_prev": seg_prev,
        "segments_latest": seg_latest,
        "cross_segment": {
            "exceptions": exceptions,
            "conflicts_count": xseg_conf,
        },
        "budgets": {
            name: {"subject": "Computing", "used": used, "total": total_cap, "remaining": remaining}
        },
        "day_choices_prev": dc_prev,
        "day_choices_latest": dc_latest,
    }

    out_dir = args.latest
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "retrospective.json").write_text(
        json.dumps(retrospective, indent=2), encoding="utf-8"
    )

    # Markdown summary
    md_lines = [
        "# Retrospective",
        f"Previous: {args.previous}",
        f"Latest: {args.latest}\n",
        "## Metrics (delta)",
        *[
            f"- {k}: {retrospective['metrics_prev'][k]} → {retrospective['metrics_latest'][k]}"
            for k in retrospective["metrics_prev"].keys()
        ],
        "\n## Per-Segment (latest)",
        *[
            f"- {seg}: blanks={seg_latest.get(seg,{}).get('blanks',0)} conflicts(tchr,cls)={seg_latest.get(seg,{}).get('teacher_conflicts',0)},{seg_latest.get(seg,{}).get('class_conflicts',0)} windows={seg_latest.get(seg,{}).get('window_violations',0)} fallback={seg_latest.get(seg,{}).get('fallback_usage',0)}"
            for seg in sorted(seg_latest.keys())
        ],
        "\n## Day Choices (changes)",
        f"- present_prev={bool(dc_prev)} present_latest={bool(dc_latest)}",
        "\n## Cross-Segment",
        f"- Cross-segment teacher conflicts (exceptions only): {xseg_conf}",
        "\n## Budgets",
        f"- {name}: used={used} total={total_cap} remaining={remaining}",
    ]
    (out_dir / "retrospective.md").write_text("\n".join(md_lines), encoding="utf-8")

    # Append one-line summary to audit
    summary_line = (
        f"rca: delta(adj={retrospective['metrics_prev']['adjacency_violations']}->"
        f"{retrospective['metrics_latest']['adjacency_violations']} same={retrospective['metrics_prev']['same_slot_repeat_score']}->"
        f"{retrospective['metrics_latest']['same_slot_repeat_score']}) xseg_conf={xseg_conf} kissi_used={used} remaining={remaining}"
    )
    for log_path in [(out_dir / "audit.log"), (out_dir / "merged" / "audit.log")]:
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write("\n" + summary_line)
        except Exception:
            pass

    print(json.dumps({"status": "ok", "latest": str(out_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
