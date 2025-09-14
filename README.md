GHIS Timetable Generator

This project is a constraints‑first scheduler for Glory Hills International School (GHIS). It builds weekly timetables across grades on a fixed daily grid, applies hard/soft rules, exports per‑grade CSV, renders a simple HTML UI, and produces validation/audit reports.

Features
- Fixed time grid with breaks/lunch encoded in data
- Hard constraints (no teacher/grade clashes) and soft penalties
- Per‑grade CSV export and a compact HTML UI (`outputs/ui/index.html`)
- Validation report (`outputs/validation.json`) and audit appendix (`outputs/audit.txt`)

Getting Started
- Requirements: Python 3.11+
- Install (dev): `make install` (or `python -m pip install -e .[dev]`)
- Generate timetable: `make run` (or `python scripts/run_generate.py`)

Outputs
- CSV: printed to stdout and optionally saved by callers
- HTML UI: `outputs/ui/index.html` (open in a browser)
- Validation JSON: `outputs/validation.json`
- Audit text: `outputs/audit.txt`

Project Structure
- `engine/` — core scheduling, data, CLI, rendering, validation
- `data/` — school structure, teachers, subjects, constraints
- `scripts/` — quick‑start runner and presubmit checks
- `outputs/` — generated artifacts (CSV/JSON/HTML/audit)
- `tests/` — pytest suite for constraints and rendering

Common Tasks
- Run generator: `make run`
- Run tests: `make test`
- Lint/format: `make lint` and `make format`
- Type‑check: `make typecheck`
- Presubmit (no blanks + clash‑free): `make presubmit`

CLI Notes
- Direct runner: `python scripts/run_generate.py` prints CSV, validation summary, and audit.
- Library entry: `engine.cli.main:run_pipeline(root, ...)` returns `(csv, validation_json_str, audit_text)`.
- Typer CLI (module): `python -m engine.cli.main --help` (if you extend the CLI).

CP-SAT Runner (Exact Branch)
- Run solver: `python3 scripts/run_cpsat.py --inputs data/ --out outputs/runs/ --timeout 120 --workers 8`
- Presubmit on output: `python3 scripts/presubmit_check.py outputs/runs/<stamp>/schedule.csv`

Data Files
- `data/structure.json` — days and time slots (with `type`: class/break/lunch)
- `data/teachers.json` — teachers and load
- `data/subjects.json` — subjects per grade/category
- `data/constraints.json` — hard rules + category groupings used in the UI legend

Development
- Formatting: Black + isort (configured via `pyproject.toml`)
- Linting: Ruff; Typing: mypy
- Tests: `pytest` (a per‑file ~500 LOC guideline is enforced via tests)

Notes
- Optional dependencies (e.g., `weasyprint` for PDF) are declared but not required for CLI runs.
- Outputs are committed by default for sharing results; adjust `.gitignore` if you prefer to exclude them.
