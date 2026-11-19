# StorageGuard / StorageAgentBench
# NOTE: the virtualenv lives in ./venv (not .venv): on macOS, files inside dot-directories under
# ~/Documents can get the "hidden" flag, which makes Python skip the editable-install .pth file.
PY ?= venv/bin/python
SG ?= venv/bin/sg

.PHONY: present present-build present-data paper paper-icrito zfs restore-sensitivity posthoc physical physical-demo colab-bundle install ollama-models test lint bench scripted smoke dev main ablations report reproduce reproduce-offline demo tune clean

install:
	uv venv --python 3.11 venv || python3.11 -m venv venv
	uv pip install --python $(PY) -e ".[dev]" || $(PY) -m pip install -e ".[dev]"

ollama-models:        ## 16k-context aliases of the local models (see configs/models.yaml)
	sh scripts/ollama_models.sh

test:
	venv/bin/pytest

lint:
	venv/bin/ruff check .

bench:                ## regenerate + validate StorageAgentBench (150 tasks)
	$(SG) generate-bench && $(SG) validate-bench

scripted:             ## LLM-free pipeline sanity check and policy-level evaluation
	$(SG) run scripted

smoke:                ## one local model on 10 dev tasks
	$(SG) run smoke

tune:                 ## threshold grid on the DEV split (scripted plans)
	$(SG) tune

dev:
	$(SG) run dev

main:
	$(SG) run main

ablations:
	$(SG) run ablations

report:               ## CSVs, statistics, figures, LaTeX tables, case studies, results/REPORT.md
	$(SG) report

reproduce:            ## everything: benchmark -> experiments (cache-aware, resumable) -> report
	$(SG) reproduce

reproduce-offline:    ## same, but only LLM-free steps + report from stored episodes
	$(SG) reproduce --skip-llm

demo:
	$(SG) demo --model scripted:naive --config storageguard

present:              ## the presentation on http://127.0.0.1:8765 (live backend + built frontend; see demo/README.md)
	$(PY) demo/server.py

present-build:        ## build the presentation frontend into demo/web/dist (needs Node 18+)
	cd demo/web && npm install && npm run build

present-data:         ## refresh the presentation's recorded fallback data in demo/web/public/data from results/
	$(PY) demo/export.py

physical:             ## replay every recorded episode on real files (no LLM) + F8 and results/physical/REPORT.md
	$(SG) physical main && $(SG) physical scripted && $(SG) physical-report

physical-demo:        ## one trap's naive plan on real files: noguard vs rbac vs storageguard
	$(SG) physical-demo --configs noguard,rbac,storageguard

clean:
	rm -rf results/runs paper/figures/* paper/tables/* results/*.csv results/REPORT.md

zfs:                  ## replay every trajectory on OpenZFS in the isolated Lima VM sg-zfs (README), then its report
	$(SG) zfs --workers 4 && $(SG) zfs-report

restore-sensitivity:  ## re-measure every restore under ZFS rollback semantics (refuse / -r), on real files
	$(SG) restore-sensitivity

posthoc:              ## post-hoc variant B4+PIT: tune on dev, evaluate on test, report (separately from B4)
	$(SG) posthoc tune && $(SG) posthoc test && $(SG) posthoc report

colab-bundle:         ## dist/storageguard_colab.zip + the Colab notebooks
	$(PY) scripts/make_colab_bundle.py

paper:                ## paper/build/main.pdf in the IEEE Access template (needs pdflatex + bibtex; run `make report` first)
	cd paper && mkdir -p build && \
	pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main >/dev/null && \
	BIBINPUTS=.: BSTINPUTS=.: bibtex build/main && \
	pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main >/dev/null && \
	pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main >/dev/null && \
	grep "Output written" build/main.log && \
	if command -v pdftoppm >/dev/null; then \
	  errs=$$(pdftoppm -r 20 -png build/main.pdf 2>&1 >/dev/null | grep -c "Error"); \
	  if [ "$$errs" -ne 0 ]; then echo "PDF has $$errs render errors (e.g. colour spaces); see paper/sections/README.md"; exit 1; fi; \
	  echo "PDF renders without errors"; fi

paper-icrito:         ## ICRITO 2027 paper: paper/icrito2027/main.pdf (anonymous) and main_named.pdf (with authors)
	cd paper/icrito2027 && for j in main main_named; do \
	  pdflatex -interaction=nonstopmode -halt-on-error $$j >/dev/null && bibtex $$j >/dev/null && \
	  pdflatex -interaction=nonstopmode -halt-on-error $$j >/dev/null && \
	  pdflatex -interaction=nonstopmode -halt-on-error $$j >/dev/null && \
	  grep "Output written" $$j.log || exit 1; \
	  pages=$$(grep -o "([0-9]* pages" $$j.log | tr -dc 0-9); \
	  if [ "$$pages" -gt 6 ]; then echo "$$j: $$pages pages, ICRITO allows 6 including references"; exit 1; fi; \
	done; rm -f *.aux *.blg *.out
