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
	python3 scripts/run_cpsat.py --segments config --timeout 120 --workers 8 --segments "JHS_B6" --mode joint
	@cd outputs/runs && latest_dir=$$(ls -1dt */ | head -1 | tr -d '/') && ln -sfn $$latest_dir latest
	$(MAKE) presubmit-seg-reports
	$(MAKE) ui-segments

solve-b15:
	@EXTRA=""; [ -n "$(BRIGHT_KISSI_BUDGET)" ] && EXTRA="--bright-kissi-budget $(BRIGHT_KISSI_BUDGET)"; \
	python3 scripts/run_cpsat.py --segment P_B1_B5 --timeout 60 --workers 8 $$EXTRA
	@cd outputs/runs && latest_dir=$$(ls -1dt */ | head -1 | tr -d '/') && ln -sfn $$latest_dir latest
	$(MAKE) presubmit-strict-report

solve-all:
	$(MAKE) diagnose-primary
	python3 scripts/run_cpsat.py --segments config --timeout 120 --workers 8 --segments "JHS_B6,P_B1_B5"
	@cd outputs/runs && latest_dir=$$(ls -1dt */ | head -1 | tr -d '/') && ln -sfn $$latest_dir latest
	$(MAKE) presubmit-seg-reports
	$(MAKE) ui-segments
	$(MAKE) presubmit-global

.PHONY: diagnose-primary
diagnose-primary:
	python3 scripts/diagnose_infeasible.py --segment P_B1_B5

.PHONY: solve-primary
solve-primary:
	$(MAKE) diagnose-primary
	python3 scripts/run_cpsat.py --segments config --timeout 120 --workers 8 --segments "P_B1_B5" --mode day-first
	@cd outputs/runs && latest_dir=$$(ls -1dt */ | head -1 | tr -d '/') && ln -sfn $$latest_dir latest
	$(MAKE) presubmit-seg-reports
	$(MAKE) ui-segments

# Retrospective + Cross-segment accountability
.PHONY: rca-latest
rca-latest:
	python3 scripts/accountability.py outputs/runs/previous outputs/runs/latest
	python3 scripts/build_ui.py --schedule outputs/runs/latest --out outputs/ui/index.html --title "GHIS Timetable – Latest" || true
	@echo "Open: outputs/ui/index.html"

.PHONY: ui-latest
ui-latest:
	python3 scripts/build_ui.py --schedule outputs/runs/latest --out outputs/ui/index.html --title "GHIS Timetable – Latest" || true
	@echo "Open: outputs/ui/index.html"

.PHONY: explain-why
explain-why:
	@[ -n "$(G)" ] && [ -n "$(S)" ] && [ -n "$(SEG)" ] || (echo "Usage: make explain-why G=<grade> S=\"<subject>\" SEG=<segment>" && exit 1)
	@outdir=outputs/explain/$(G)/$(subst ,_,$(S)); mkdir -p $$outdir; \
	python3 scripts/explain_why.py --grade $(G) --subject "$(S)" --segment $(SEG) --out $$outdir; \
	python3 scripts/build_ui.py --schedule outputs/runs/latest --out outputs/ui/index.html --title "GHIS Timetable – Latest" --explain-dir $$outdir --highlight-grade $(G) --highlight-subject "$(S)" || true
	@echo "Open: outputs/ui/index.html"

# Auto-detect segments and write JSON
.PHONY: detect-segments
detect-segments:
	@mkdir -p outputs
	python3 scripts/segment_detect.py > outputs/segments.json

# Multi-segment solve and merged output
.PHONY: solve-segments
solve-segments:
	$(MAKE) detect-segments
	python3 scripts/run_cpsat.py --segments auto --timeout 120 --workers 8

# Per-segment presubmits and HTML reports
.PHONY: presubmit-seg-reports
presubmit-seg-reports:
	@for dir in outputs/runs/latest/*/ ; do \
	  if [ -f "$$dir/schedule.csv" ]; then \
	    echo "Strict presubmit for $$dir"; \
	    python3 scripts/presubmit_check.py --strict --emit-metrics $$dir/schedule.csv | tee $$dir/presubmit.txt; \
	    python3 scripts/templates/make_report.py $$dir/metrics.json $$dir/presubmit.txt > $$dir/report.html; \
	  fi; \
	done; \
	python3 scripts/build_ui.py --schedule outputs/runs/latest --out outputs/ui/index.html --title "GHIS Timetable – Latest" || true; \
	echo "Open: outputs/ui/index.html"

# Global guardrail presubmit (exception teachers across segments only)
.PHONY: presubmit-global
presubmit-global:
	python3 scripts/presubmit_global.py --segments-root outputs/runs/latest --exceptions configs/segments.toml | tee outputs/runs/latest/merged/presubmit.txt; \
	python3 scripts/build_ui.py --schedule outputs/runs/latest --out outputs/ui/index.html --title "GHIS Timetable – Latest" || true; \
	echo "Open: outputs/ui/index.html"

# Build per-segment and merged grid UIs
.PHONY: ui-segments
ui-segments:
	@for dir in outputs/runs/latest/*/ ; do \
	  if [ -f "$$dir/schedule.csv" ]; then \
	    python3 scripts/build_ui.py --schedule $$dir --out $$dir/index.html --title "Segment: $$dir"; \
	  fi; \
	done; \
	python3 scripts/build_ui.py --schedule outputs/runs/latest --out outputs/ui/index.html --title "GHIS Timetable – Latest" || true; \
	echo "Open: outputs/ui/index.html"
