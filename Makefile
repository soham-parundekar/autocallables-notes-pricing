# Pipeline for the autocallable note pricing project.
#
# The stages are ordered by dependency and each writes to data/processed/, so a
# stage can be rerun on its own once the ones before it have run.
#
# One warning about `data`: it captures the CURRENT session's option chains.
# The results in reports/ rest on the preserved 2026-10-09 snapshot already in
# data/raw/option_chains/, and `surfaces` onwards reproduces them from there.
# Run `data` to point the project at a new pricing week, not to regenerate the
# published numbers.

PYTHON ?= python3
CONTACT ?= $(shell git config user.name) $(shell git config user.email)

.PHONY: all data surfaces validate analysis report check-report test scan screen paper \
        paper-tables paper-figures audit-paper bibliography clean-processed

all: surfaces validate analysis test report scan

data:
	$(PYTHON) src/data/option_snapshot.py SPX NDX RUT DJX SMH SPY QQQ IWM DIA \
		--out data/raw/option_chains
	$(PYTHON) scripts/fetch_reference_data.py

# Fit and cache the five SVI surfaces, with the butterfly and calendar audit.
surfaces:
	$(PYTHON) -m src.vol.calibrate

# The gate: does the local-vol engine return the vanilla prices it was built on?
validate:
	$(PYTHON) -m src.vol.reprice_check

# Price the notes, invert the funding spreads, Greeks and model risk.
# Resumable: rerunning skips notes already in data/processed/analysis.json.
analysis:
	$(PYTHON) -m src.analysis.run_analysis

# Which notes exist in the filing population, and which can be priced at all.
screen:
	$(PYTHON) -m src.data.edgar_notes --contact "$(CONTACT)"

# Tables, figures, then the report itself. make_report.py refuses to render
# unless every number the prose quotes resolves against the saved results, so
# this target failing means the report and the run have diverged.
report:
	$(PYTHON) scripts/make_tables.py
	$(PYTHON) scripts/make_figures.py
	$(PYTHON) scripts/make_report.py --require 6

# Does the committed report match the committed results? Used by the audit.
check-report:
	$(PYTHON) scripts/make_report.py --check --require 6

# Regenerate the manuscript's bibliography from the verified citation bank.
# Entries come from Crossref, so this needs the network; --offline reuses the
# cached responses under paper/.bibcache/.
bibliography:
	$(PYTHON) scripts/make_bibliography.py

# The manuscript's tables, generated from the frozen results rather than typed.
# A number in the paper that cannot be traced to a saved result is the failure
# mode this target exists to prevent.
paper-tables:
	$(PYTHON) scripts/make_paper_tables.py

# Every number in the manuscript against the frozen results. Catches figures
# with no source; see the script's docstring for what it cannot catch.
audit-paper:
	$(PYTHON) scripts/audit_paper_numbers.py
	$(PYTHON) scripts/audit_paper_claims.py --quiet

# The manuscript uses the same figures as the report rather than its own copies,
# so they are synced at build time. Editing one in paper/figures/ would be
# overwritten here, which is the point: reports/figures/ is the single source.
paper-figures:
	cp reports/figures/*.png paper/figures/

# Compile the manuscript. Requires a TeX distribution with newtx and apacite.
paper: bibliography paper-tables paper-figures audit-paper
	cd paper && latexmk -pdf -interaction=nonstopmode main.tex

test:
	$(PYTHON) -m pytest tests/ -q

# Pre-publication check: secrets, stray artifacts, documentation drift.
scan:
	$(PYTHON) scripts/scan_repository.py

# Drops derived artifacts only. data/raw/ is never touched.
clean-processed:
	rm -f data/processed/*.json data/processed/*.csv
