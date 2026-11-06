# Tether Web UI host

This is an unreleased Python workspace package. The distribution name describes
the planned external artifact; use the repository workspace until a matching
release is published.

`novirhiza-tether-web-ui` provides independent ASGI hosting for compiled Tether
browser assets. It does not depend on the core runtime or HTTP adapter and does
not own identity, API, or `ClientHost` lifecycle.

Build the browser reference assets and package the resulting verified manifest
before building the wheel. `create_static_app(*, settings=UiSettings(...))`
serves only the files listed in `tether-assets.json`, verifies their digests,
applies mount-aware asset paths, and returns the safe API discovery document.
The caller composes this ASGI app with its API and owns both lifecycles.

See the repository [React guide](../../docs/react.md) for the browser packages
and [HTTP API guide](../../docs/web-api.md) for the identity/API composition.
