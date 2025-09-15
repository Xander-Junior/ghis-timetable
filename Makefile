.PHONY: install format lint typecheck test run presubmit presubmit-strict run-heuristic-quick solve-jhs solve-b15 solve-all

install:
	python -m pip install -e .[dev] || true

format:
	black . || true
	isort . || true

lint:
	isort .
	black .
	flake8 || true

typecheck:
	mypy . || true

test:
	pytest -q engine/specs/presubmit_golden_spec.py engine/specs/presubmit_jhs_rules_spec.py || true

run:
	python scripts/run_generate.py

presubmit:
	python3 scripts/presubmit_check.py outputs/timetable.csv

# Strict presubmit: defaults if env not provided
MAX_ADJ ?= 2
MAX_SAME_SLOT ?= 6
presubmit-strict:
	MAX_ADJ=$(MAX_ADJ) MAX_SAME_SLOT=$(MAX_SAME_SLOT) python3 scripts/presubmit_check.py --strict outputs/runs/latest/schedule.csv

# Generate a schedule and symlink it under outputs/runs/latest/schedule.csv
run-heuristic-quick:
	- python3 scripts/run_generate.py || true
	mkdir -p outputs/runs/latest
	ln -sf ../timetable.csv outputs/runs/latest/schedule.csv
	# normalize artifacts for CI convenience
	cp -f outputs/audit.txt outputs/audit.log || true
	# create placeholder metrics.json if missing
	[ -f outputs/metrics.json ] || echo '{}' > outputs/metrics.json

# Save strict output and HTML report into outputs/runs/latest
presubmit-strict-report:
	@echo "Running presubmit strict and teeing output…"
	@python3 scripts/presubmit_check.py --strict --emit-metrics outputs/runs/latest/schedule.csv | tee outputs/runs/latest/presubmit.txt
	@[ -f outputs/metrics.json ] && cp outputs/metrics.json outputs/runs/latest/metrics.json || true
	@[ -f outputs/validation.json ] && cp outputs/validation.json outputs/runs/latest/validation.json || true
	@cp scripts/templates/presubmit_report.html outputs/runs/latest/report.html || true
	@echo "Open outputs/runs/latest/report.html in a browser."

# CP-SAT segment runs
solve-jhs:
	python3 scripts/run_cpsat.py --segment JHS_B6 --timeout 60 --workers 8
	@cd outputs/runs && latest_dir=$$(ls -1dt */ | head -1 | tr -d '/') && ln -sfn $$latest_dir latest
	$(MAKE) presubmit-strict-report

solve-b15:
	@EXTRA=""; [ -n "$(BRIGHT_KISSI_BUDGET)" ] && EXTRA="--bright-kissi-budget $(BRIGHT_KISSI_BUDGET)"; \
	python3 scripts/run_cpsat.py --segment P_B1_B5 --timeout 60 --workers 8 $$EXTRA
	@cd outputs/runs && latest_dir=$$(ls -1dt */ | head -1 | tr -d '/') && ln -sfn $$latest_dir latest
	$(MAKE) presubmit-strict-report

solve-all:
	- python3 scripts/run_cpsat.py --segment ALL --timeout 60 --workers 8 || true
	@cd outputs/runs; \
	jhs_dir=""; \
	for d in $$(ls -1dt */); do dd=$${d%/}; if [ -f "$$dd/audit.log" ] && grep -q 'segment=JHS_B6' "$$dd/audit.log"; then jhs_dir="$$dd"; break; fi; done; \
	if [ -n "$$jhs_dir" ]; then ln -sfn "$$jhs_dir" latest; else latest_dir=$$(ls -1dt */ | head -1 | tr -d '/'); ln -sfn "$$latest_dir" latest; fi
	$(MAKE) presubmit-strict-report

# Retrospective + Cross-segment accountability
.PHONY: rca-latest
rca-latest:
	python3 scripts/accountability.py outputs/runs/previous outputs/runs/latest
