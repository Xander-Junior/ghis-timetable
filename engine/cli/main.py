from __future__ import annotations

import logging
from pathlib import Path
from typing import List

try:
    import typer  # type: ignore
except Exception:  # pragma: no cover
    typer = None  # type: ignore

import json

from ..data.loader import load_data
from ..data.registry import ConstraintRegistry, OccupancyLedger, SubjectQuotas
from ..data.teachers import TeacherDirectory
from ..models.timetable import Timetable
from ..render.csv_out import csv_blocks, write_csv_blocks
from ..render.html_ui import write_html_ui
from ..scheduler import fill_schedule, repair_schedule, seed_schedule
from ..validate.checks import validate_all
from ..validate.report import format_validation_report, write_validation_report


def _setup_logging(project_root: Path) -> None:
    logs_dir = project_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(logs_dir / "engine.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def run_pipeline(
    project_root: Path,
    *,
    log_level: int | None = None,
    ucmas_day: str | None = None,
    max_repairs: int = 1,
    max_swaps: int = 200,
    restarts: int | None = None,
    tabu: int | None = None,
    neighborhoods: List[str] | None = None,
    penalty_same_time: int | None = None,
    penalty_adjacent: int | None = None,
    deficit_weight: int | None = None,
    relax_electives: object = False,
    # Neighborhood bounds (optional overrides)
    rr_depth: int | None = None,
    rr_nodes: int | None = None,
    rr_attempts_per_blank: int | None = None,
    kempe_depth: int | None = None,
    kempe_nodes: int | None = None,
) -> tuple[str, str, str]:
    _setup_logging(project_root)
    if log_level is not None:
        logging.getLogger().setLevel(log_level)
    loaded = load_data(project_root)
    structure = loaded.structure
    grades: List[str] = structure["grades"]
    days: List[str] = structure["days"]
    time_slots: List[dict] = structure["time_slots"]
    constraints = loaded.constraints
    if ucmas_day:
        constraints.setdefault("ucmas_policy", {})["day_override"] = ucmas_day
    registry = ConstraintRegistry(
        collision_rules=constraints.get("collision_rules", {}),
        weekly_quotas=constraints.get("weekly_quotas", {}),
        time_windows=constraints.get("time_windows", []),
        pe_policy=constraints.get("pe_policy", {}),
        ucmas_policy=constraints.get("ucmas_policy", {}),
        extra_curricular=constraints.get("extra_curricular", {}),
        anti_patterns=constraints.get("anti_patterns", {}),
        immutables=constraints.get("immutables", {}),
    )
    quotas = SubjectQuotas(registry.weekly_quotas)
    if relax_electives:
        quotas.set_relax_electives(relax_electives)
    teacher_dir = TeacherDirectory(loaded.teachers)
    ledger = OccupancyLedger()
    tt = Timetable()

    tt, seed_audit = seed_schedule(tt, ledger, grades, days, time_slots, constraints, teacher_dir)
    tt, fill_audit = fill_schedule(tt, ledger, grades, days, time_slots, quotas, teacher_dir)
    total_repair_audit: list[str] = []
    for _ in range(max_repairs):
        tt, repair_audit = repair_schedule(
            tt,
            ledger,
            grades,
            days,
            teacher_dir,
            quotas,
            max_swaps=max_swaps,
            time_slots=time_slots,
            penalty_same_time=(
                penalty_same_time
                if penalty_same_time is not None
                else constraints.get("anti_patterns", {}).get("penalty_same_time", 10)
            ),
            penalty_adjacent=(
                penalty_adjacent
                if penalty_adjacent is not None
                else constraints.get("anti_patterns", {}).get("penalty_adjacent", 3)
            ),
            deficit_weight=(deficit_weight if deficit_weight is not None else 100),
            neighborhoods=neighborhoods
            or ["grade_day", "grade_period", "stuck_grade", "blank_rr", "kempe_period_swap"],
            tabu_k=(tabu or 0),
            rr_depth=rr_depth,
            rr_nodes=rr_nodes,
            rr_attempts_per_blank=rr_attempts_per_blank,
            kempe_depth=kempe_depth,
            kempe_nodes=kempe_nodes,
        )
        total_repair_audit.extend(repair_audit)

    report = validate_all(tt, grades, days, time_slots, registry.weekly_quotas)

    outputs_dir = project_root / "outputs"
    write_validation_report(report, outputs_dir)
    csv = csv_blocks(tt, grades, days, time_slots)
    write_csv_blocks(csv, outputs_dir)
    ui_path = write_html_ui(tt, structure, constraints, outputs_dir)
    # Persist intermediate JSON schedule
    json_dir = outputs_dir / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    schedule_json = [
        {
            "grade": a.grade,
            "day": a.day,
            "slot": a.slot_id,
            "subject": a.subject,
            "teacher": a.teacher,
            "immutable": a.immutable,
        }
        for a in sorted(tt.all(), key=lambda x: (x.grade, x.day, x.slot_id))
    ]
    with (json_dir / "schedule.json").open("w", encoding="utf-8") as f:
        json.dump(schedule_json, f, indent=2)

    audit_text = "\n".join(
        ["Seeded placements:"] + seed_audit + [""] + ["Repairs:"] + total_repair_audit
    )
    with (outputs_dir / "audit.txt").open("w", encoding="utf-8") as f:
        f.write(audit_text)

    return csv, format_validation_report(report), audit_text


if typer is not None:  # pragma: no cover
    app = typer.Typer(add_completion=False, help="GHIS timetable generator")

    @app.command("generate")
    def cli_generate(
        ucmas_day: str = typer.Option("Tuesday", help="Override UCMAS seeding day"),
        log_level: str = typer.Option("INFO", help="Log level"),
        max_repairs: int = typer.Option(2, help="Number of repair iterations"),
        max_swaps: int = typer.Option(200, help="Max neighborhood iterations per repair"),
        restarts: int = typer.Option(1, help="Independent restarts (randomized)"),
        tabu: int = typer.Option(0, help="Tabu tenure (0=off)"),
        neighborhoods: str = typer.Option(
            "grade_day,grade_period,stuck_grade,blank_rr,kempe_period_swap",
            help="Neighborhood set (comma-separated)",
        ),
        rr_depth: int | None = typer.Option(None, help="Override blank_rr DFS depth (default 4)"),
        rr_nodes: int | None = typer.Option(
            None, help="Override blank_rr node cap per attempt (default 200)"
        ),
        rr_attempts_per_blank: int | None = typer.Option(
            None, help="Override blank_rr attempts per blank (default 3)"
        ),
        kempe_depth: int | None = typer.Option(
            None, help="Override kempe max chain depth (default 6)"
        ),
        kempe_nodes: int | None = typer.Option(
            None, help="Override kempe node/scan cap (default 300)"
        ),
        penalty_same_time: int = typer.Option(
            10, help="Penalty for same subject same time across classes"
        ),
        penalty_adjacent: int = typer.Option(
            3, help="Penalty for adjacent same subject across classes"
        ),
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        level = getattr(logging, log_level.upper(), logging.INFO)
        # inject penalties into constraints overrides via pipeline
        csv, validation, audit = run_pipeline(
            root,
            log_level=level,
            ucmas_day=ucmas_day,
            max_repairs=max_repairs,
            max_swaps=max_swaps,
            restarts=restarts,
            tabu=tabu,
            neighborhoods=[s.strip() for s in neighborhoods.split(",") if s.strip()],
            penalty_same_time=penalty_same_time,
            penalty_adjacent=penalty_adjacent,
            rr_depth=rr_depth,
            rr_nodes=rr_nodes,
            rr_attempts_per_blank=rr_attempts_per_blank,
            kempe_depth=kempe_depth,
            kempe_nodes=kempe_nodes,
        )
        print(csv)
        print(validation)
        print(audit)

    @app.command("validate")
    def cli_validate() -> None:
        root = Path(__file__).resolve().parents[2]
        _, validation, _ = run_pipeline(root)
        print(validation)

    @app.command("export-csv")
    def cli_export_csv() -> None:
        root = Path(__file__).resolve().parents[2]
        csv, _, _ = run_pipeline(root)
        print(csv)
