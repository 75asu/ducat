.PHONY: install lint test build image clean local-up local-down local-logs local-metrics local-reset

install:
	pip install -e ".[push,dev,gcp]"

lint:
	ruff check .

test:
	pytest -q

build:
	python -m build

image:
	docker build -t ducat:dev .

clean:
	rm -rf dist build *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# -- Local stack: ducat -> Prometheus -> Grafana. Nothing leaves this machine. ---
# Useful when you want to look at real billing data without pushing anything into
# a shared or production observability stack.
LOCAL := deploy/local/docker-compose.yml

# Historical Console CSVs get mounted at the same in-container path the cluster
# uses, so the adapter has one code path. Keep the host folder OUTSIDE this repo:
# it is public, and the exports carry real project names and spend.
#   make local-up BILLING_CSV_DIR=~/path/to/csvs
export BILLING_CSV_DIR ?= ./billing-csv

local-up:  ## start the local cost stack (Grafana :3000, Prometheus :9091)
	@docker compose -f $(LOCAL) up -d --build
	@echo ">>> Grafana:    http://localhost:3000   dashboard: 'ducat , cloud cost (local)'"
	@echo ">>> Prometheus: http://localhost:9091"
	@echo ">>> historical CSVs: $(BILLING_CSV_DIR) -> /var/lib/ducat/billing-csv"
	@echo ">>> first scrape can take a minute (it runs a BigQuery query). 'make local-logs' to watch."

local-down:  ## stop the local stack, KEEPING the Prometheus + Grafana volumes
	@docker compose -f $(LOCAL) down

local-logs:  ## tail ducat's logs (adapter + query errors surface here)
	@docker compose -f $(LOCAL) logs -f ducat

local-metrics:  ## print ducat's raw metrics from inside the container
	@docker compose -f $(LOCAL) exec -T ducat python -c \
		"import urllib.request;print(urllib.request.urlopen('http://localhost:9090/metrics').read().decode())" \
		| grep -E '^ducat_' | head -40

# Deliberately gated: this DELETES the Prometheus + Grafana volumes. Guarded so it
# can never run by accident or by autocomplete -- you must pass CONFIRM=1.
local-reset:  ## delete local volumes for a clean slate -- requires CONFIRM=1
	@if [ "$(CONFIRM)" != "1" ]; then \
		echo "REFUSING: this deletes the ducat-local Prometheus + Grafana volumes."; \
		echo "Volumes affected: ducat-local_prom-data, ducat-local_grafana-data"; \
		echo "Re-run with: make local-reset CONFIRM=1"; \
		exit 1; \
	fi
	@docker compose -f $(LOCAL) down --volumes
