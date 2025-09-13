from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class LoadedData:
    structure: Dict[str, Any]
    subjects: Dict[str, Any]
    teachers: Dict[str, Any]
    constraints: Dict[str, Any]


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_data(root: Path) -> LoadedData:
    data_dir = root / "data"
    return LoadedData(
        structure=load_json(data_dir / "structure.json"),
        subjects=load_json(data_dir / "subjects.json"),
        teachers=load_json(data_dir / "teachers.json"),
        constraints=load_json(data_dir / "constraints.json"),
    )

