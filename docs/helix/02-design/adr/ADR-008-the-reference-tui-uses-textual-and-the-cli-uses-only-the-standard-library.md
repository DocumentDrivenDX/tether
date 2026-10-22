---
ddx:
  id: ADR-008
  type: adr
  activity: design
  status: accepted
  owner: Erik LaBianca
  updated: 2026-10-22
  authoring:
    home: repo
  links:
    - id: ADR-001
      kind: informed_by
    - id: ADR-004
      kind: informed_by
    - id: prd
      kind: informs
---

# ADR-008: The Reference TUI Uses Textual and the CLI Uses Only the Standard Library

## Problem

The library ships two reference clients (PRD FR-29, FR-30) so that a developer can try the harness, and test the client API, within seconds of cloning the repository. A terminal UI needs a framework that fits an asynchronous library, can be tested without a human, and does not become a dependency of the core. A command-line client needs almost nothing.

## Decision

We will build the reference terminal UI on Textual (an asynchronous Python framework for terminal user interfaces) as an optional extra, and the command-line client on the standard library alone (`argparse`, plain and JSON output).

- **Textual for the TUI.** `tether.tui` is the only module that imports Textual, in the `tui` extra. The TUI is a consumer of the client API (CONTRACT-003) and holds no engine or store internals.
- **Standard library for the CLI.** `tether.cli` uses `argparse` and prints plain text or JSON lines. It adds no dependency beyond the core and the `pydantic-settings` its composition root already needs.
- **Both are included in the repository's development environment**, so that `uv run tether` works after a clone (PRD FR-33).
- **TUI tests do not need a terminal.** The TUI is tested through Textual's headless test driver, to be confirmed in the slice; if it falls short, the fallback is a pseudo-terminal test.

## Why We Chose This

### Over `prompt_toolkit`, `urwid`, or a `rich` live display
They are capable, but they are not built around an asynchronous application model, and the TUI is a live view over an asynchronous feed.

### Over a web interface as the reference client
It would exercise a server adapter the library deliberately does not contain (ADR-004) and would need a browser to try.

### Over `typer` or `click` for the CLI
They make a richer command line, at the price of a dependency for a client that needs a handful of subcommands.

## Consequences

**Good**: An asynchronous TUI that sits naturally on the client API's feed. A CLI with no extra dependency. Both are optional extras, so the core stays at `pydantic` and `anyio`.

**Trade-offs**: Textual is a substantial dependency, pinned exactly and confined to one module. The CLI's output is plain; a developer who wants richer output uses the TUI.

**Drift signals to watch**: An import of Textual outside `tether.tui`. A third-party dependency added to the CLI. A client importing a store or the engine. A TUI test that needs a real terminal.

## Concern Impact

- **No concern impact**: `python-uv` covers the stack; this ADR is the coverage AGENTS.md requires for Textual as a dependency.
