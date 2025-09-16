from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple


def load_structure(root: Path) -> dict:
    import json as _json

    with (root / "data" / "structure.json").open("r", encoding="utf-8") as f:
        return _json.load(f)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    struct = load_structure(root)
    slots = struct.get("time_slots", [])
    # Sequence of IDs in order, with types
    seq: List[str] = [t["id"] for t in slots]
    typ: Dict[str, str] = {t["id"]: t.get("type", "teaching") for t in slots}
    # Map id -> start
    starts: Dict[str, str] = {t["id"]: t["start"] for t in slots}

    # Teaching IDs excluding PE (T3) and EC (T9) conflicts for Friday
    teach_ids = [sid for sid in seq if typ.get(sid) == "teaching"]
    teach_ids_no_t3_t9 = [sid for sid in teach_ids if sid not in {"T3", "T9"}]

    # B9 double candidates (adjacent or straddle across exactly one non-teaching), not touching T3/T9
    b9_pairs: List[Tuple[str, str]] = []
    for i in range(len(seq)):
        a = seq[i]
        if typ.get(a) != "teaching" or a in {"T3", "T9"}:
            continue
        # adjacent
        if i + 1 < len(seq):
            b = seq[i + 1]
            if typ.get(b) == "teaching" and b not in {"T3", "T9"}:
                b9_pairs.append((a, b))
        # straddle
        if i + 2 < len(seq):
            mid = seq[i + 1]
            b = seq[i + 2]
            if (
                typ.get(mid) in {"break", "lunch"}
                and typ.get(b) == "teaching"
                and b not in {"T3", "T9"}
            ):
                b9_pairs.append((a, b))

    # Friday Bright demand summary
    # Available teaching slots on Friday (excluding T3 and T9):
    available = teach_ids_no_t3_t9[:]  # e.g., [T1,T2,T5,T6,T8]
    # Demand: B9 English uses 2 slots; B7A, B7B, B8A, B8B each need 1 on Friday -> total 6
    demand = 2 + 4
    feasible_capacity = len(available)
    capacity_ok = feasible_capacity >= demand

    summary = {
        "friday_available_slots": available,
        "b9_double_candidates": [list(p) for p in b9_pairs],
        "bright_friday_required_uses": demand,
        "bright_friday_available_slots": feasible_capacity,
        "capacity_feasible": capacity_ok,
        "explanation": (
            "Impossible to schedule: need 6 Bright English uses across Friday but only "
            f"{feasible_capacity} teaching slots exist (excluding PE T3 and EC/OpenRevision T9)."
            if not capacity_ok
            else "Sufficient slots exist in principle; still need to check clashes with other subjects."
        ),
    }

    out_dir = root / "outputs" / "probes"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "friday_bright.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

