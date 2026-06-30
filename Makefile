.PHONY: install lint test build image clean

install:
	pip install -e ".[push,dev]"

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
