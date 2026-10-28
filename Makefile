CHECK_BASE ?= HEAD

.PHONY: check pr-check lint hooks build
check lint:
	uv run --locked python scripts/quality.py "$(CHECK_BASE)"

pr-check:
	uv run --locked python scripts/quality.py --pr "$(CHECK_BASE)"

hooks:
	git config core.hooksPath .githooks

build:
	uv build
