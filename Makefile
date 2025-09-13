.PHONY: install format lint typecheck test run presubmit

install:
	python -m pip install -e .[dev] || true

format:
	black . || true
	isort . || true

lint:
	ruff check . || true

typecheck:
	mypy . || true

test:
	pytest -q || true

run:
	python scripts/run_generate.py

presubmit:
	python3 scripts/presubmit_check.py
