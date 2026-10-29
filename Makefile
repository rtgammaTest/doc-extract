.PHONY: help install install-dev sql test demo eval gate clean deploy bootstrap

PROJECT ?= $(shell gcloud config get-value project 2>/dev/null)
REGION  ?= us-east4
DATASET ?= doc_extract
FIXTURE ?= eval/fixtures/sample_agreement.txt
SECTIONS ?= 9.1 9.2 9.3

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime dependencies
	pip install -r requirements.txt

install-dev:  ## Install everything, including pytest
	pip install -r requirements-dev.txt

sql:  ## Regenerate the DDL from extractor/schema.py
	DATASET=$(DATASET) python3 sql/generate.py

test:  ## Run the test suite (pytest if present, stdlib runner otherwise)
	@python3 -c "import pytest" 2>/dev/null && pytest -q || python3 run_tests.py

demo:  ## Extract the sample agreement and score it, with no cloud and no cost
	rm -rf out
	python3 -m extractor.cli $(FIXTURE) --sections $(SECTIONS)
	python3 eval/score.py --label "regex baseline"

eval:  ## Score whatever is in out/ against eval/gold
	python3 eval/score.py

gate:  ## Fail if the eval regressed
	python3 eval/gate.py --report eval/reports/latest.json \
		--baseline eval/reports/baseline.json \
		--min-accuracy 0.80 --max-regression 0.03

bootstrap:  ## Create the bucket, dataset, tables and service account
	PROJECT=$(PROJECT) REGION=$(REGION) DATASET=$(DATASET) bash scripts/bootstrap.sh

deploy:  ## Build and deploy the Cloud Run service and the Eventarc trigger
	PROJECT=$(PROJECT) REGION=$(REGION) DATASET=$(DATASET) bash scripts/deploy.sh

clean:  ## Remove local output
	rm -rf out eval/reports/latest.md eval/reports/latest.json __pycache__ */__pycache__
