from __future__ import annotations

from pathlib import Path


EXCLUDE = {"__init__.py"}


def count_loc(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    return sum(1 for _ in text.splitlines())


def test_line_budget_per_file() -> None:
    root = Path(__file__).resolve().parents[1]
    budget = 500
    offenders: list[tuple[str, int]] = []
    for p in root.rglob("*.py"):
        if p.name in EXCLUDE:
            continue
        if "outputs" in p.parts:
            continue
        loc = count_loc(p)
        if loc > budget:
            offenders.append((str(p), loc))
    assert not offenders, f"Files exceeding {budget} LOC: {offenders}"

