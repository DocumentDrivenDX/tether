<!-- helix:publish audience=developer page=embedding kind=tutorial voice=onboarding budget=1200 reviewed=2026-10-29/root/- -->
<!-- helix:source src/tether/client/_composition/host.py sha256:785e232f6d49934e -->
<!-- helix:source src/tether/client/protocol.py sha256:b2abda7561603dcc -->
<!-- helix:source src/tether/client/testing.py sha256:8fbbed8d4c8ab343 -->
<!-- helix:source pyproject.toml sha256:973d053005dce8a9 -->

# Embed Tether

Tether's public Python entry point is `tether.client`. The host composes model,
storage, policy, tools, limits, and optional ports; your application owns identity
resolution, request handling, and the host lifecycle. The [HTTP adapter](web-api.md)
and [remote terminal](remote-terminal.md) are optional consumers of this same client
protocol.

## Install the core

This guide targets the unreleased `0.2.0.dev0` candidate. Published core `0.2.0`
has an earlier client facade without `ClientHost`; it cannot replace this
candidate. Candidate Python interface packages require the exact core cohort.
In the checkout, `uv sync --frozen` resolves that workspace source. The registry
install below applies after a release includes the APIs used in this guide.

```sh
uv add novirhiza-tether
```

The core package metadata requires Python 3.11 or later. Optional interfaces stay in separate
distributions or extras: `novirhiza-tether[tui]` for the reference terminal UI,
`novirhiza-tether[remote]` for the HTTP client transport, and
`novirhiza-tether[postgres]` for PostgreSQL storage. Add only the surfaces your
application uses.

## Compose an explicit host

Provide all required settings. This fabricated example uses the public testing
profile and process-lifetime storage; it does not make restart-durability claims.
For an application, replace the fabricated model, policy, tools, and storage with
the implementations your application owns.

```python
from tether.client import (
    Actor,
    AgentSpec,
    ClientHost,
    ClientLimits,
    ClientSettings,
    ModelExecutionLimits,
    ModelResponse,
    VolatileSettings,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model


async def run() -> None:
    context = fabricated_model_context()
    settings = ClientSettings(
        model=scripted_model(
            [ModelResponse(content="Fabricated reply", finish_reason="stop")]
        ),
        storage=VolatileSettings(),
        policy=demo_policy(),
        client_limits=ClientLimits(
            max_page_size=100,
            max_call_ids=8,
            max_read_bytes=1_048_576,
            max_lookup_entries=1_000,
            read_timeout_seconds=10,
            heartbeat_interval_seconds=15,
            shutdown_timeout_seconds=5,
            follower_queue_size=32,
            max_followers=8,
            max_active_turns=8,
            max_request_bytes=65_536,
        ),
        model_limits=ModelExecutionLimits(
            max_response_bytes=65_536,
            max_tool_argument_bytes=16_384,
            max_attempts=2,
            request_timeout_seconds=30,
            max_retry_delay_seconds=2,
        ),
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated example",
                instructions="Answer the fabricated request.",
                model="demo",
            ),
        ),
        default_spec="demo",
        resume="off",
    )

    async with await ClientHost.open(settings) as host:
        actor = Actor(id="fabricated-user")
        client = host.client(actor)
        session = await client.create(spec="demo")
        result = await (
            await client.send(session.id, "Say hello", spec="demo")
        ).result()
        print(result.messages[-1].content)
```

`ClientHost.open()` returns a ready host and `async with` initiates its bounded
shutdown. `await host.aclose()` returns `False` if physical work remains;
`host.drained` reports whether shutdown has settled. After initiating shutdown,
use `await host.wait_closed()` when your application needs to await actual
resource release. Cancelling that waiter does not cancel retained cleanup.
Keep caller-owned dependencies alive until physical work has settled.

A host owns
only resources it creates from its selected settings. `ProvidedStorage` and ports
passed to `ClientHost.from_ports()` are borrowed; close those in their owning
application. A borrowed host requires `resume="off"`. `VolatileSettings()` also
requires `resume="off"` and loses state when the process exits. Use
`LocalJsonlSettings(root=Path(...))` for the explicit local JSONL profile. Use an
explicit PostgreSQL profile when the application needs shared durable storage;
the `postgres` extra installs its adapter but does not configure a database or
identity policy for you.

## Bind identity per request

Resolve the authenticated application user on each request and pass an `Actor` to
`host.client(actor)`. Do not share a client across principals. If the actor carries
a credential, Tether does not store it in settings or session history; your
authentication layer remains responsible for credential refresh and revocation.
Keep model credentials, application infrastructure credentials, and user
credentials in their separate owners.

Advanced integrations use typed public ports from `tether.client` and the
interfaces governed by CONTRACT-001 through CONTRACT-005. Call
`ClientHost.from_ports(settings, ports=...)`; do not construct an engine or import
private stores to customize behavior. Every injected port remains caller-owned.

For process-only tests, `tether.client.testing.fabricated_storage()` supplies an
isolated `ProvidedStorage` fixture. Borrowers must close before leaving the
fixture; it cannot establish restart durability or external database behavior:

```python
from dataclasses import replace

from tether.client import ClientHost
from tether.client.testing import fabricated_storage

async with fabricated_storage() as storage:
    test_settings = replace(settings, storage=storage, resume="off")
    async with await ClientHost.open(test_settings) as host:
        client = host.client(actor)
        # Exercise the application against the public client protocol.
```

## Optional terminal UI and remote client

The reference TUI accepts a `ClientProtocol` and borrows it. A launcher that owns
the host or remote transport closes it after the UI exits. For a Python HTTP
client, install `novirhiza-tether[remote]` and use
`tether.client.remote.RemoteClient` with a credential provider that returns the
current credential for each request and reconnect. See [Remote terminal](remote-terminal.md)
for the explicit Databricks CLI profile path.

## Public interfaces and qualification

The stable consumer surface is `tether.client`; optional web packages use the
separate `tether_web_api` and `tether_web_ui` namespaces. See the
[versioned API guide](web-api.md), the [React guide](react.md), and
`docs/helix/02-design/contracts/CONTRACT-006-embeddable-client-and-web-api.md`.
Deployment compatibility, storage behavior, and supported version combinations
must be stated with their selected profiles and evidence. A local fabricated
example does not establish a production profile.
