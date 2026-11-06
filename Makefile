CHECK_BASE ?= HEAD
WEB_PROFILE ?=
WEB_APP_ORIGIN ?=
WEB_LIVE_RECEIPT ?=
WEB_PACKAGE_WORKDIR ?=
PERF_BASELINES ?= docs/helix/04-build/evidence/web-platform/performance
PERF_OUTPUT ?= /private/tmp/tether-performance-current
PERF_APPS_MEASUREMENT ?=
PERF_RESERVATION ?=
PERF_LOCK ?= /private/tmp/tether-performance.lock
PERF_MACHINE_PROFILE ?= /private/tmp/tether-performance-machine-profile.json

# Keep this user-provided path out of Make's recursive command-line expansion.
# Workflow dispatch supplies it as an environment value, which the recipe
# forwards as one quoted argv element.
ifeq ($(origin PERF_APPS_MEASUREMENT),command line)
$(error PERF_APPS_MEASUREMENT must be supplied in the environment so paths are passed literally)
endif
ifeq ($(origin PERF_RESERVATION),command line)
$(error PERF_RESERVATION must be supplied in the environment so paths are passed literally)
endif
ifeq ($(origin PERF_MACHINE_PROFILE),command line)
$(error PERF_MACHINE_PROFILE must be supplied in the environment so paths are passed literally)
endif
export PERF_APPS_MEASUREMENT
export PERF_RESERVATION
export PERF_MACHINE_PROFILE

.PHONY: check pr-check lint hooks build web-boundaries web-client-contract web-api-contract web-ui-check web-package-check web-check web-platform-check web-release-check web-release-postcheck web-performance-check
check lint:
	uv run --locked python scripts/quality.py "$(CHECK_BASE)"

pr-check:
	uv run --locked python scripts/quality.py --pr "$(CHECK_BASE)"

hooks:
	git config core.hooksPath .githooks

build:
	uv build

# Separate manual/reference-runner measurement gate. Functional quality commands
# use a configurable 1,800s emergency child watchdog; profile measurement limits
# remain the separately declared performance workload deadlines.
web-performance-check:
	@test -n "$${PERF_RESERVATION}" || { printf '%s\n' 'PERF_RESERVATION is required in the environment.' >&2; exit 2; }
	uv run --locked python scripts/web_performance.py --baselines "$(PERF_BASELINES)" --output "$(PERF_OUTPUT)" $(if $(PERF_APPS_MEASUREMENT),--apps-measurement "$${PERF_APPS_MEASUREMENT}") --reservation-file "$${PERF_RESERVATION}" --machine-profile "$${PERF_MACHINE_PROFILE}" --lock-file "$(PERF_LOCK)"

web-boundaries web-client-contract web-api-contract web-ui-check web-package-check web-check web-platform-check web-release-check web-release-postcheck:
	uv run --locked python scripts/web_quality.py $(patsubst web-%,%,$@) --base "$(CHECK_BASE)" --profile "$(WEB_PROFILE)" $(if $(WEB_PACKAGE_WORKDIR),--package-workdir "$(WEB_PACKAGE_WORKDIR)") $(if $(WEB_APP_ORIGIN),--app-origin "$(WEB_APP_ORIGIN)") $(if $(WEB_LIVE_RECEIPT),--live-receipt "$(WEB_LIVE_RECEIPT)")
