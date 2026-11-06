# Independent Python embedder example

This project is a fabricated consumer of the installed `novirhiza-tether`,
`novirhiza-tether-web-api`, and `novirhiza-tether-web-ui` distributions. Its app
composition imports the public `tether.client`, `tether_web_api`, and
`tether_web_ui` surfaces; it constructs an explicit public `ClientHost`, injects
a fixed local-only `Authenticator`, and mounts the HTTP and static UI factories
in one ASGI host.

The sample authenticator maps every request to one fabricated actor and the
model returns scripted content. It is suitable for local contract tests only.
Replace authentication, model, policy, tools, storage, limits, and logging
before deploying any application. It has not been qualified for production or
Databricks Apps.

Install a released package pair and test dependencies into this project's
environment:

```sh
uv sync --extra test
uv run pytest
```

Run an ASGI server with a small launcher that creates the app from an explicit
data root and closes its returned host during lifespan shutdown. The test shows
the intended ownership boundary without starting a listener or contacting a
model provider.

The current checkout test command, when running before these package versions
are published, is:

```sh
PYTHONPATH=examples/embed/src uv run --no-sync pytest -o addopts= -q examples/embed/tests
```

The root checkout command above is a development test, not evidence of a clean
third-party installation. Independent wheel/sdist installation and version
matrix evidence is tracked separately in the web-platform implementation plan.
