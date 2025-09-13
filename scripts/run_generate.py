from pathlib import Path
import sys

# Ensure project root on sys.path for direct script execution
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from engine.cli.main import run_pipeline


def main() -> None:
    csv, validation, audit = run_pipeline(
        root,
        ucmas_day="Tuesday",
        max_repairs=6,
        penalty_same_time=12,
        penalty_adjacent=4,
    )
    print(csv)
    print(validation)
    print(audit)


if __name__ == "__main__":
    # If invoked as module, run pipeline; Typer app is available via `python -m engine.cli.main` too.
    main()
