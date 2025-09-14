from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SolverConfig:
    timeout_sec: int = 120
    workers: int = 8
    weight_adjacent: int = 6
    weight_same_slot: int = 2
    weight_teacher_gaps: int = 0
    penalty_supervised_study: int = 10_000


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _load_all(project_root: Path) -> Tuple[dict, dict, dict, dict]:
    from engine.data.loader import load_data

    data = load_data(project_root)
    return data.structure, data.subjects, data.teachers, data.constraints


def _grade_base(g: str) -> str:
    # e.g., B7A -> B7; B9 -> B9
    for i, ch in enumerate(g):
        if ch.isalpha() and i > 0 and g[i - 1].isdigit():
            return g[:i]
    return g


def _teachers_list(teachers_json: dict) -> List[dict]:
    return list(teachers_json.get("teachers", []))


def _teacher_name(t: dict) -> str:
    return t.get("name", t.get("id", ""))


def _teacher_can_teach(t: dict, subject: str, grade: str) -> bool:
    subs = set(t.get("subjects", []))
    if subject not in subs:
        return False
    gbase = _grade_base(grade)
    allowed = set(t.get("grades", []))
    return (gbase in allowed) or (grade in allowed)


def _structure_maps(structure: dict) -> Tuple[List[str], List[str], List[dict], Dict[str, dict]]:
    grades = list(structure["grades"])
    days = list(structure["days"])
    time_slots = list(structure["time_slots"])
    ts_by_id = {ts["id"]: ts for ts in time_slots}
    return grades, days, time_slots, ts_by_id


def _teaching_slot_ids(time_slots: List[dict]) -> List[str]:
    return [ts["id"] for ts in time_slots if ts.get("type") == "teaching"]


def _slot_id_for_times(time_slots: List[dict], start: str, end: str) -> str:
    for ts in time_slots:
        if ts["start"] == start and ts["end"] == end:
            return ts["id"]
    return ""


def _format_csv_rows(
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
    fixed_subjects: Dict[Tuple[str, str, str], str],
    chosen: Dict[Tuple[str, str, str], Tuple[str, str]],
) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for g in grades:
        for d in days:
            for ts in time_slots:
                sid = ts["id"]
                start, end, typ = ts["start"], ts["end"], ts.get("type", "teaching")
                if typ == "break":
                    out.append({"Grade": g, "Day": d, "PeriodStart": start, "PeriodEnd": end, "Subject": "Break", "Teacher": ""})
                    continue
                if typ == "lunch":
                    out.append({"Grade": g, "Day": d, "PeriodStart": start, "PeriodEnd": end, "Subject": "Lunch", "Teacher": ""})
                    continue
                fixed = fixed_subjects.get((g, d, sid))
                if fixed is not None and not (g == "B9" and d == "Friday" and sid == "T9"):
                    out.append({"Grade": g, "Day": d, "PeriodStart": start, "PeriodEnd": end, "Subject": fixed, "Teacher": ""})
                    continue
                subj, teacher = chosen.get((g, d, sid), ("", ""))
                out.append({"Grade": g, "Day": d, "PeriodStart": start, "PeriodEnd": end, "Subject": subj, "Teacher": teacher})
    return out


def _write_run_outputs(run_dir: Path, rows: List[Dict[str, str]], metrics: Dict[str, Any], audit_lines: List[str]) -> Dict[str, Any]:
    schedule_path = run_dir / "schedule.csv"
    with schedule_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Grade", "Day", "PeriodStart", "PeriodEnd", "Subject", "Teacher"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    metrics_path = run_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    audit_path = run_dir / "audit.log"
    with audit_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(audit_lines))
    return {"schedule_path": str(schedule_path), "metrics_path": str(metrics_path), "audit_path": str(audit_path)}


def _compute_metrics(rows: List[Dict[str, str]], structure: Dict[str, Any]) -> Dict[str, Any]:
    days = structure["days"]
    time_slots = structure["time_slots"]
    teaching_times = {(ts["start"], ts["end"]) for ts in time_slots if ts.get("type") == "teaching"}
    blanks = 0
    teacher_conflicts = 0
    class_conflicts = 0
    teacher_slots: Dict[Tuple[str, str, str], int] = {}
    class_slots: Dict[Tuple[str, str, str], int] = {}
    for r in rows:
        if (r["PeriodStart"], r["PeriodEnd"]) not in teaching_times:
            continue
        if not r["Subject"]:
            blanks += 1
        tkey = (r.get("Teacher") or "", r["Day"], r["PeriodStart"])  # teacher,time key
        if tkey[0]:
            teacher_slots[tkey] = teacher_slots.get(tkey, 0) + 1
        ckey = (r["Grade"], r["Day"], r["PeriodStart"]) 
        class_slots[ckey] = class_slots.get(ckey, 0) + 1
    teacher_conflicts = sum(1 for v in teacher_slots.values() if v > 1)
    class_conflicts = sum(1 for v in class_slots.values() if v > 1)

    # Windows + adjacency + same-slot repeats
    window_violations = 0
    for r in rows:
        if r["Subject"] == "Twi" and _grade_base(r["Grade"]) in {"B7", "B8", "B9"} and r["Day"] not in {"Wednesday", "Friday"}:
            window_violations += 1
    order = {ts["id"]: i for i, ts in enumerate(time_slots, start=1)}
    adjacency_violations = 0
    b9_english_adjacencies = 0
    by_gd: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
    for r in rows:
        sid = _slot_id_for_times(time_slots, r["PeriodStart"], r["PeriodEnd"])
        if sid:
            by_gd.setdefault((r["Grade"], r["Day"]), []).append((order.get(sid, 0), r["Subject"]))
    for (g, d), seq in by_gd.items():
        seq.sort()
        for i in range(len(seq) - 1):
            a, b = seq[i][1], seq[i + 1][1]
            if not a or a in {"Break", "Lunch"}:
                continue
            if a == b:
                if g == "B9" and a == "English":
                    b9_english_adjacencies += 1
                else:
                    adjacency_violations += 1
    same_slot_repeat_score = 0
    by_gst: Dict[Tuple[str, str, str], int] = {}
    for r in rows:
        sid = _slot_id_for_times(time_slots, r["PeriodStart"], r["PeriodEnd"])
        if not sid:
            continue
        k = (r["Grade"], r["Subject"], sid)
        if r["Subject"] not in {"", "Break", "Lunch", "Extra Curricular"}:
            by_gst[k] = by_gst.get(k, 0) + 1
    for _, c in by_gst.items():
        if c > 1:
            same_slot_repeat_score += (c - 1)

    fallback_usage = sum(1 for r in rows if r["Subject"] == "Supervised Study")

    return {
        "blanks": blanks,
        "teacher_conflicts": teacher_conflicts,
        "class_conflicts": class_conflicts,
        "window_violations": window_violations,
        "adjacency_violations": adjacency_violations,
        "b9_english_adjacencies": b9_english_adjacencies,
        "same_slot_repeat_score": same_slot_repeat_score,
        "fallback_usage": fallback_usage,
    }


def _subject_targets_by_grade(
    grades: List[str],
    subjects: List[str],
    teachers: List[dict],
    constraints: dict,
    time_slots: List[dict],
) -> Dict[str, Dict[str, Tuple[int, int]]]:
    weekly = constraints.get("weekly_quotas", {})
    ucmas = constraints.get("ucmas_policy", {})
    ucmas_present = set(ucmas.get("present_grades", []))
    ucmas_absent = set(ucmas.get("absent_grades", []))
    # feasibility per (s,g)
    feasible: Dict[Tuple[str, str], bool] = {}
    for g in grades:
        for s in subjects:
            ok = False
            for t in teachers:
                if _teacher_can_teach(t, s, g):
                    ok = True
                    break
            feasible[(s, g)] = ok
    targets: Dict[str, Dict[str, Tuple[int, int]]] = {g: {} for g in grades}
    for g in grades:
        gbase = _grade_base(g)
        for s in subjects:
            if s in {"Supervised Study", "Extra Curricular"}:
                targets[g][s] = (0, 0)
                continue
            if s == "UCMAS":
                if g in ucmas_absent or gbase in ucmas_absent:
                    targets[g][s] = (0, 0)
                elif g in ucmas_present or gbase in ucmas_present:
                    q = int(weekly.get("UCMAS", 0))
                    targets[g][s] = (q, q)
                else:
                    targets[g][s] = (0, 0)
                continue
            q = int(weekly.get(s, 0))
            if feasible.get((s, g), False):
                targets[g][s] = (q, q)
            else:
                targets[g][s] = (0, 0)
    return targets


def _collect_fixed_subjects(
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
) -> Dict[Tuple[str, str, str], str]:
    fixed: Dict[Tuple[str, str, str], str] = {}
    for g in grades:
        for d in days:
            for ts in time_slots:
                sid = ts["id"]
                if ts.get("type") in {"break", "lunch"}:
                    continue
                fs = ts.get("fixed_subject")
                if fs:
                    fixed[(g, d, sid)] = fs
    return fixed


def _build_and_solve(
    *,
    grades: List[str],
    days: List[str],
    time_slots: List[dict],
    subjects_all: List[str],
    teachers: List[dict],
    fixed_subjects: Dict[Tuple[str, str, str], str],
    targets: Dict[str, Dict[str, Tuple[int, int]]],
    cfg: SolverConfig,
    segments: Dict[str, str] | None = None,
    cross_seg_teachers: List[str] | None = None,
    pe_bands: Dict[str, str] | None = None,
    teacher_overrides: Dict[str, Dict[str, Any]] | None = None,
    teacher_weekly_caps: Dict[str, int] | None = None,
) -> Tuple[bool, Dict[Tuple[str, str, str], Tuple[str, str]], Dict[str, Any], List[str]]:
    try:
        from ortools.sat.python import cp_model
    except Exception as e:
        raise RuntimeError(
            "OR-Tools (ortools) is not installed. Please install it: python -m pip install ortools"
        ) from e

    teach_ids = _teaching_slot_ids(time_slots)
    order = {ts["id"]: i for i, ts in enumerate(time_slots, start=1)}

    is_fixed = dict(fixed_subjects)
    if ("B9", "Friday", "T9") in is_fixed:
        del is_fixed[("B9", "Friday", "T9")]

    teacher_names = [_teacher_name(t) for t in teachers]
    model = cp_model.CpModel()

    X: Dict[Tuple[str, str, str, str, str], cp_model.IntVar] = {}
    subj_presence: Dict[Tuple[str, str, str, str], cp_model.IntVar] = {}

    # Teacher overrides helper: returns allowed days if any override matches
    overrides = teacher_overrides or {}
    def _override_days(name: str, subj: str, g: str) -> set[str] | None:
        tc = overrides.get(name)
        if not tc:
            return None
        subs = set(tc.get("subjects", []) or [])
        if subj not in subs:
            return None
        grades_ok = set(tc.get("grades", []) or [])
        gb = _grade_base(g)
        if g not in grades_ok and gb not in grades_ok:
            return None
        days_ok = set(tc.get("days_allowed", []) or [])
        return days_ok or None

    def allowed_teachers(g: str, s: str) -> List[str]:
        return [name for t, name in zip(teachers, teacher_names) if _teacher_can_teach(t, s, g)]

    # Per-cell single assignment
    for g in grades:
        for d in days:
            for ts in time_slots:
                sid = ts["id"]
                if ts.get("type") != "teaching":
                    continue
                if (g, d, sid) in is_fixed:
                    continue
                cand_subjects = list(subjects_all)
                if (g == "B9" and d == "Friday" and sid == "T9"):
                    cand_subjects = ["English"]
                sum_subj_terms = []
                for s in cand_subjects:
                    prs = model.NewBoolVar(f"prs[{g},{d},{sid},{s}]")
                    subj_presence[(g, d, sid, s)] = prs
                    feas = allowed_teachers(g, s)
                    if not feas and s not in {"Supervised Study", "UCMAS", "P.E."}:
                        model.Add(prs == 0)
                        continue
                    xterms = []
                    for rname in feas:
                        var = model.NewBoolVar(f"x[{g},{d},{sid},{s},{rname}]")
                        X[(g, d, sid, s, rname)] = var
                        xterms.append(var)
                    if xterms:
                        model.Add(prs == sum(xterms))
                    sum_subj_terms.append(prs)
                if sum_subj_terms:
                    model.Add(sum(sum_subj_terms) == 1)

    # Teacher capacity (segment-aware if segments provided)
    segs = segments or {}
    cross_seg = set(cross_seg_teachers or [])
    for d in days:
        for sid in teach_ids:
            for t, rname in zip(teachers, teacher_names):
                terms = []
                for g in grades:
                    if (g, d, sid) in is_fixed:
                        continue
                    for s in subjects_all:
                        var = X.get((g, d, sid, s, rname))
                        if var is not None:
                            terms.append(var)
                if not terms:
                    continue
                if segs:
                    # Enforce within-segment capacity; if cross-seg teacher, also enforce across all
                    by_seg: Dict[str, List] = {}
                    for g in grades:
                        for s in subjects_all:
                            var = X.get((g, d, sid, s, rname))
                            if var is None:
                                continue
                            sg = segs.get(_grade_base(g), "")
                            by_seg.setdefault(sg, []).append(var)
                    for sg, lst in by_seg.items():
                        if lst:
                            model.Add(sum(lst) <= 1)
                    if rname in cross_seg:
                        model.Add(sum(terms) <= 1)
                else:
                    model.Add(sum(terms) <= 1)

    # Weekly quotas
    for g in grades:
        for s in subjects_all:
            lo, hi = targets[g].get(s, (0, 0))
            prs_terms = []
            for d in days:
                for sid in teach_ids:
                    if (g, d, sid) in is_fixed and not (g == "B9" and d == "Friday" and sid == "T9" and s == "English"):
                        continue
                    prs = subj_presence.get((g, d, sid, s))
                    if prs is not None:
                        prs_terms.append(prs)
            if prs_terms:
                if lo == hi:
                    model.Add(sum(prs_terms) == lo)
                else:
                    model.Add(sum(prs_terms) >= lo)
                    model.Add(sum(prs_terms) <= hi)

    # Twi window (B7–B9 only Wed/Fri)
    for g in grades:
        if _grade_base(g) in {"B7", "B8", "B9"}:
            for d in days:
                if d in {"Wednesday", "Friday"}:
                    continue
                for sid in teach_ids:
                    prs = subj_presence.get((g, d, sid, "Twi"))
                    if prs is not None:
                        model.Add(prs == 0)

    # P.E. bands (Friday-only, hard pins from configs)
    bands = pe_bands or {}
    if bands:
        for g in grades:
            gb = _grade_base(g)
            band = bands.get(gb)
            if not band:
                continue
            sid_req = {"P1": "T1", "P2": "T2", "P3": "T3"}.get(str(band))
            if not sid_req:
                continue
            # Exactly one on Friday at required period
            prs_req = subj_presence.get((g, "Friday", sid_req, "P.E."))
            if prs_req is not None:
                model.Add(prs_req == 1)
            # Forbid elsewhere
            for d in days:
                for sid in teach_ids:
                    prs = subj_presence.get((g, d, sid, "P.E."))
                    if prs is None:
                        continue
                    if not (d == "Friday" and sid == sid_req):
                        model.Add(prs == 0)

    # B9 English on Wed and Fri
    for day_needed in ["Wednesday", "Friday"]:
        prs_terms = []
        for sid in teach_ids:
            prs = subj_presence.get(("B9", day_needed, sid, "English"))
            if prs is not None:
                prs_terms.append(prs)
        if prs_terms:
            model.Add(sum(prs_terms) >= 1)

    # B9 Friday T9 English
    prs_b9_fri_t9 = subj_presence.get(("B9", "Friday", "T9", "English"))
    if prs_b9_fri_t9 is not None:
        model.Add(prs_b9_fri_t9 == 1)
    for s in subjects_all:
        if s == "English":
            continue
        prs = subj_presence.get(("B9", "Friday", "T9", s))
        if prs is not None:
            model.Add(prs == 0)
    # Enforce Sir Bright Dey for that cell (if present in teacher set)
    for tn in teacher_names:
        var = X.get(("B9", "Friday", "T9", "English", tn))
        if var is None:
            continue
        if tn == "Sir Bright Dey":
            model.Add(var == 1)
        else:
            model.Add(var == 0)

    # English teacher/day splits for B7 and B8
    for g in ("B7", "B8"):
        # Exactly 1 English on Wed taught by Sir Bright Dey
        wed_vars = []
        for sid in teach_ids:
            v = X.get((g, "Wednesday", sid, "English", "Sir Bright Dey"))
            if v is not None:
                wed_vars.append(v)
        if wed_vars:
            model.Add(sum(wed_vars) == 1)
        # Exactly 1 English on Fri taught by Sir Bright Dey
        fri_vars = []
        for sid in teach_ids:
            v = X.get((g, "Friday", sid, "English", "Sir Bright Dey"))
            if v is not None:
                fri_vars.append(v)
        if fri_vars:
            model.Add(sum(fri_vars) == 1)
        # Exactly 2 English on Mon/Tue/Thu taught by Harriet; forbid Sir Bright on these days
        mtt_h = []
        for d in ("Monday", "Tuesday", "Thursday"):
            for sid in teach_ids:
                vh = X.get((g, d, sid, "English", "Harriet Akasraku"))
                if vh is not None:
                    mtt_h.append(vh)
                vb = X.get((g, d, sid, "English", "Sir Bright Dey"))
                if vb is not None:
                    model.Add(vb == 0)
        if mtt_h:
            model.Add(sum(mtt_h) == 2)
        # On Wed/Fri forbid Harriet on English
        for d in ("Wednesday", "Friday"):
            for sid in teach_ids:
                vh = X.get((g, d, sid, "English", "Harriet Akasraku"))
                if vh is not None:
                    model.Add(vh == 0)

    # Global teacher-day overrides (TOML) — except allow B9 English by SBD any day
    if teacher_overrides:
        for g in grades:
            for d in days:
                for sid in teach_ids:
                    for s in subjects_all:
                        for tn in teacher_names:
                            var = X.get((g, d, sid, s, tn))
                            if var is None:
                                continue
                            if g == "B9" and s == "English" and tn == "Sir Bright Dey":
                                continue
                            allowed_days = overrides.get(tn, {}).get("days_allowed") if overrides else None
                            if allowed_days:
                                # Validate applicability
                                subs = set(overrides.get(tn, {}).get("subjects", []) or [])
                                grs = set(overrides.get(tn, {}).get("grades", []) or [])
                                gb = _grade_base(g)
                                if s in subs and (g in grs or gb in grs):
                                    if d not in set(allowed_days):
                                        model.Add(var == 0)

    # Teacher weekly caps (e.g., Bright Kissi budget)
    if teacher_weekly_caps:
        for tn, cap in teacher_weekly_caps.items():
            terms = []
            for g in grades:
                for d in days:
                    for sid in teach_ids:
                        for s in subjects_all:
                            v = X.get((g, d, sid, s, tn))
                            if v is not None:
                                terms.append(v)
            if terms:
                model.Add(sum(terms) <= int(cap))

    # Objective: adjacency + same-slot + fallback
    soft_terms = []
    for g in grades:
        for d in days:
            seq = sorted(teach_ids, key=lambda x: order.get(x, 0))
            for i in range(len(seq) - 1):
                t1, t2 = seq[i], seq[i + 1]
                for s in subjects_all:
                    p1 = subj_presence.get((g, d, t1, s))
                    p2 = subj_presence.get((g, d, t2, s))
                    if p1 is None or p2 is None:
                        continue
                    adj = model.NewBoolVar(f"adj[{g},{d},{t1}-{t2},{s}]")
                    model.Add(adj <= p1)
                    model.Add(adj <= p2)
                    model.Add(p1 + p2 - adj <= 1)
                    if g == "B9" and s == "English":
                        # will handle via excess penalty
                        pass
                    else:
                        soft_terms.append((cfg.weight_adjacent, adj))
    # B9 English adjacency excess
    b9_pairs = []
    for d in days:
        seq = sorted(teach_ids, key=lambda x: order.get(x, 0))
        for i in range(len(seq) - 1):
            p1 = subj_presence.get(("B9", d, seq[i], "English"))
            p2 = subj_presence.get(("B9", d, seq[i + 1], "English"))
            if p1 is None or p2 is None:
                continue
            adj = model.NewBoolVar(f"b9e_adj[{d},{seq[i]}-{seq[i+1]}]")
            model.Add(adj <= p1)
            model.Add(adj <= p2)
            model.Add(p1 + p2 - adj <= 1)
            b9_pairs.append(adj)
    if b9_pairs:
        b9_sum = model.NewIntVar(0, 20, "b9_eng_adj_sum")
        model.Add(b9_sum == sum(b9_pairs))
        # Hard rule: at most one daily double-block across the week
        model.Add(b9_sum <= 1)

    # Same-slot repeats
    for g in grades:
        for s in subjects_all:
            if s in {"", "Break", "Lunch", "Extra Curricular"}:
                continue
            for sid in teach_ids:
                occ_terms = []
                for d in days:
                    prs = subj_presence.get((g, d, sid, s))
                    if prs is not None:
                        occ_terms.append(prs)
                if not occ_terms:
                    continue
                try:
                    from ortools.sat.python import cp_model
                except Exception:  # already imported
                    pass
                occ = model.NewIntVar(0, len(days), f"occ[{g},{s},{sid}]")
                model.Add(occ == sum(occ_terms))
                excess = model.NewIntVar(0, len(days), f"occ_excess[{g},{s},{sid}]")
                model.Add(excess >= occ - 1)
                model.Add(excess >= 0)
                soft_terms.append((cfg.weight_same_slot, excess))

    # Supervised Study penalty
    for g in grades:
        for d in days:
            for sid in teach_ids:
                prs = subj_presence.get((g, d, sid, "Supervised Study"))
                if prs is not None:
                    soft_terms.append((cfg.penalty_supervised_study, prs))

    if soft_terms:
        model.Minimize(sum(w * v for (w, v) in soft_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(cfg.timeout_sec)
    solver.parameters.num_search_workers = int(cfg.workers)

    status = solver.Solve(model)
    audit: List[str] = [
        f"workers={cfg.workers} timeout={cfg.timeout_sec}",
        f"weights: adjacent={cfg.weight_adjacent} same_slot={cfg.weight_same_slot} supervised={cfg.penalty_supervised_study}",
        f"status={solver.StatusName(status)} objective={solver.ObjectiveValue() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 'n/a'}",
    ]

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return False, {}, {"status": solver.StatusName(status)}, audit

    chosen: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    for g in grades:
        for d in days:
            for sid in teach_ids:
                if (g, d, sid) in is_fixed:
                    continue
                picked_s: Optional[str] = None
                for s in subjects_all:
                    prs = subj_presence.get((g, d, sid, s))
                    if prs is not None and solver.Value(prs) == 1:
                        picked_s = s
                        break
                if not picked_s:
                    chosen[(g, d, sid)] = ("", "")
                    continue
                rname = ""
                for tdict, tn in zip(teachers, teacher_names):
                    var = X.get((g, d, sid, picked_s, tn))
                    if var is not None and solver.Value(var) == 1:
                        rname = tn
                        break
                chosen[(g, d, sid)] = (picked_s, rname)

    return True, chosen, {"status": solver.StatusName(status), "objective": solver.ObjectiveValue()}, audit


def _relax_non_core_minima(
    grades: List[str],
    targets: Dict[str, Dict[str, Tuple[int, int]]],
    categories: dict,
    capacity_by_grade: Dict[str, int],
) -> Tuple[Dict[str, Dict[str, Tuple[int, int]]], Dict[str, int]]:
    core = set(categories.get("Core", []))
    reductions: Dict[str, int] = {}
    new_targets = {g: dict(v) for g, v in targets.items()}
    for g, cap in capacity_by_grade.items():
        total_req = sum(q for s, (q, _) in new_targets[g].items() if q > 0)
        excess = max(0, total_req - cap)
        if excess <= 0:
            continue
        while excess > 0:
            picked = None
            for s, (lo, hi) in new_targets[g].items():
                if s not in core and lo > 0:
                    picked = s
                    break
            if not picked:
                break
            lo, hi = new_targets[g][picked]
            new_targets[g][picked] = (lo - 1, max(0, hi - 1))
            reductions[g] = reductions.get(g, 0) + 1
            excess -= 1
    return new_targets, reductions


def solve(
    inputs_dir: Path,
    out_root: Path,
    config: Optional[SolverConfig] = None,
    *,
    segment: str | None = None,
    segments_toml: str | None = None,
    teacher_overrides_toml: str | None = None,
    teacher_weekly_caps: Dict[str, int] | None = None,
) -> Dict[str, Any]:
    cfg = config or SolverConfig()
    project_root = Path(__file__).resolve().parents[2]
    structure, subjects_json, teachers_json, constraints = _load_all(project_root)
    grades, days, time_slots, ts_by_id = _structure_maps(structure)
    teachers = _teachers_list(teachers_json)
    subjects_all = list(subjects_json.get("canonical", [])) + ["Supervised Study"]
    fixed_subjects = _collect_fixed_subjects(grades, days, time_slots)
    targets = _subject_targets_by_grade(grades, subjects_all, teachers, constraints, time_slots)

    # Segments and overrides
    segs: Dict[str, str] = {}
    cross_seg_teachers: List[str] = []
    pe_bands: Dict[str, str] = {}
    teacher_overrides_map: Dict[str, Dict[str, Any]] = {}
    try:
        import tomllib  # py311+
        if segments_toml and Path(segments_toml).exists():
            with open(segments_toml, "rb") as f:
                cfg_t = tomllib.load(f)
            segs = {k: str(v) for k, v in (cfg_t.get("segments", {}) or {}).items()}
            cross_seg_teachers = list((cfg_t.get("cross_segment_teachers", {}) or {}).get("names", []))
            pe_bands = {k: str(v) for k, v in (cfg_t.get("pe_bands", {}) or {}).items()}
        if teacher_overrides_toml and Path(teacher_overrides_toml).exists():
            with open(teacher_overrides_toml, "rb") as f:
                cfg_t2 = tomllib.load(f)
            teacher_overrides_map = cfg_t2.get("teacher_constraints", {}) or {}
    except Exception:
        pass

    # Filter by segment if requested
    seg_select = (segment or "").strip()
    if seg_select and seg_select in {"JHS_B6", "P_B1_B5"} and segs:
        grades = [g for g in grades if segs.get(_grade_base(g)) == seg_select]

    teach_ids = _teaching_slot_ids(time_slots)
    capacity_by_grade: Dict[str, int] = {}
    for g in grades:
        cap = 0
        for d in days:
            for sid in teach_ids:
                if (g, d, sid) in fixed_subjects and not (g == "B9" and d == "Friday" and sid == "T9"):
                    continue
                cap += 1
        capacity_by_grade[g] = cap
    # Allow fallback "Supervised Study" up to capacity per grade
    for g in grades:
        if "Supervised Study" in targets[g]:
            lo, _ = targets[g]["Supervised Study"]
            targets[g]["Supervised Study"] = (0, capacity_by_grade[g])

    # Pre-trim non-core totals to fit capacity by grade to avoid guaranteed infeasibility
    targets, initial_reductions = _relax_non_core_minima(grades, targets, constraints.get("categories", {}), capacity_by_grade)

    ok, chosen, stats, audit = _build_and_solve(
        grades=grades,
        days=days,
        time_slots=time_slots,
        subjects_all=subjects_all,
        teachers=teachers,
        fixed_subjects=fixed_subjects,
        targets=targets,
        cfg=cfg,
        segments=segs,
        cross_seg_teachers=cross_seg_teachers,
        pe_bands=pe_bands,
        teacher_overrides=teacher_overrides_map,
        teacher_weekly_caps=teacher_weekly_caps,
    )

    stamp = _timestamp()
    run_dir = out_root / "runs" / stamp
    _ensure_dir(run_dir)
    audit_lines: List[str] = [
        "CP-SAT: exact model run",
        f"config: workers={cfg.workers} timeout={cfg.timeout_sec}",
        f"initial_noncore_reductions={initial_reductions}",
    ]
    if seg_select:
        audit_lines.append(f"segment={seg_select}")

    if not ok:
        relaxed_targets, red = _relax_non_core_minima(grades, targets, constraints.get("categories", {}), capacity_by_grade)
        audit_lines.append(f"Relaxation applied (non-core): {red}")
        ok2, chosen2, stats2, audit2 = _build_and_solve(
            grades=grades,
            days=days,
            time_slots=time_slots,
            subjects_all=subjects_all,
            teachers=teachers,
            fixed_subjects=fixed_subjects,
            targets=relaxed_targets,
            cfg=cfg,
        )
        audit_lines.extend(audit2)
        if not ok2:
            rows: List[Dict[str, str]] = _format_csv_rows(grades, days, time_slots, fixed_subjects, {})
            metrics = _compute_metrics(rows, structure)
            audit_lines.append("Result: infeasible after one relaxation.")
            paths = _write_run_outputs(run_dir, rows, metrics, audit_lines)
            return {"run_dir": str(run_dir), **paths, "status": stats2.get("status", "INFEASIBLE")}
        chosen = chosen2
        stats = stats2
        ok = True

    rows = _format_csv_rows(grades, days, time_slots, fixed_subjects, chosen)
    metrics = _compute_metrics(rows, structure)
    audit_lines.extend([f"status={stats.get('status')} objective={stats.get('objective')}", f"metrics: {json.dumps(metrics)}"])
    paths = _write_run_outputs(run_dir, rows, metrics, audit_lines)
    return {"run_dir": str(run_dir), **paths, "metrics": metrics, "status": stats.get("status")}
