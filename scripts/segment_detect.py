from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set, Tuple


def load_data(root: Path) -> tuple[dict, dict, dict, dict]:
    import sys
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from engine.data.loader import load_data as _ld  # type: ignore

    data = _ld(root)
    return data.structure, data.subjects, data.teachers, data.constraints


def _grade_base(g: str) -> str:
    for i, ch in enumerate(g):
        if ch.isalpha() and i > 0 and g[i - 1].isdigit():
            return g[:i]
    return g


def _detect_segments_auto(structure: dict, teachers: dict) -> Dict[str, List[str]]:
    # Build bipartite graph: teacher <-> base grade
    # Use simple adjacency sets, then union-find connected components over grades via teacher links
    tlist = list(teachers.get("teachers", []))
    grades: List[str] = [str(g) for g in structure.get("grades", [])]
    g_bases: Set[str] = {_grade_base(g) for g in grades}
    neigh_t: Dict[str, Set[str]] = {}  # teacher -> set(base grades)
    neigh_g: Dict[str, Set[str]] = {gb: set() for gb in g_bases}  # base grade -> set(teachers)
    for t in tlist:
        tn = t.get("name") or t.get("id") or ""
        if not tn:
            continue
        allowed = set(str(x) for x in (t.get("grades") or []))
        # normalize like B7A->B7
        allowed_bases = {_grade_base(x) for x in allowed}
        neigh_t[tn] = set()
        for gb in allowed_bases:
            if gb in neigh_g:
                neigh_t[tn].add(gb)
                neigh_g[gb].add(tn)

    # BFS components over grades using teacher links
    unvisited = set(g_bases)
    comps: List[Set[str]] = []
    while unvisited:
        start = next(iter(unvisited))
        stack = [start]
        seen_g: Set[str] = set()
        seen_t: Set[str] = set()
        while stack:
            gb = stack.pop()
            if gb in seen_g:
                continue
            seen_g.add(gb)
            for tn in neigh_g.get(gb, set()):
                if tn in seen_t:
                    continue
                seen_t.add(tn)
                for gb2 in neigh_t.get(tn, set()):
                    if gb2 not in seen_g:
                        stack.append(gb2)
        comps.append(seen_g)
        unvisited -= seen_g

    # Name segments SEG1, SEG2, ... and expand base grades back to full grades present in structure
    segs: Dict[str, List[str]] = {}
    for idx, gb_set in enumerate(comps, start=1):
        seg_id = f"SEG{idx}"
        segs[seg_id] = [g for g in grades if _grade_base(g) in gb_set]
    return segs


def _load_config_override(root: Path) -> tuple[Dict[str, List[str]], List[str]]:
    segs: Dict[str, List[str]] = {}
    xseg: List[str] = []
    try:
        import tomllib

        with (root / "configs" / "segments.toml").open("rb") as f:
            t = tomllib.load(f)
        mapping: Dict[str, str] = t.get("segments", {}) or {}
        segs_rev: Dict[str, List[str]] = {}
        for g, seg in mapping.items():
            segs_rev.setdefault(str(seg), []).append(str(g))
        segs = segs_rev
        xseg = list((t.get("cross_segment_teachers", {}) or {}).get("names", []))
    except Exception:
        pass
    return segs, xseg


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    structure, subjects, teachers, constraints = load_data(root)

    segs_cfg, xseg_cfg = _load_config_override(root)
    if segs_cfg:
        out = {k: sorted(v) for k, v in segs_cfg.items()}
        summary = {"segments": out, "cross_segment_teachers": xseg_cfg}
    else:
        auto = _detect_segments_auto(structure, teachers)
        summary = {
            "segments": {k: sorted(v) for k, v in auto.items()},
            "cross_segment_teachers": [],
        }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
