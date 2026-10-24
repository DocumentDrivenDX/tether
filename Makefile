CHECK_BASE ?= HEAD

.PHONY: check lint hooks build
check lint:
	uv run --locked python scripts/quality.py "$(CHECK_BASE)"

hooks:
	git config core.hooksPath .githooks

build:
	uv build
