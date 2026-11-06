<!-- helix:publish audience=developer page=web-api kind=how-to voice=onboarding budget=1200 reviewed=2026-10-29/root/- -->
<!-- helix:source packages/web-api/src/tether_web_api/__init__.py sha256:369b7e2fb3f6a034 -->
<!-- helix:source packages/web-api/src/tether_web_api/boundary.py sha256:ec3126b9e719ffae -->
<!-- helix:source packages/web-api/src/tether_web_api/factory.py sha256:2e50c44636c43709 -->
<!-- helix:source packages/web-api/src/tether_web_api/openapi.py sha256:b158d6f238ec7482 -->
<!-- helix:source packages/web-api/src/tether_web_api/routes/events.py sha256:7a5634fe19e4b4bf -->
<!-- helix:source packages/web-api/src/tether_web_api/routes/turns.py sha256:6fc50e3e23b10e63 -->
<!-- helix:source packages/web-api/src/tether_web_api/schema.py sha256:4c773a8f6569c926 -->
<!-- helix:source packages/web-api/src/tether_web_api/settings.py sha256:b1390ce2702f6ba7 -->
<!-- helix:source packages/web-api/schema/http-v1.json sha256:646914c863e6cccc -->
<!-- helix:source src/tether/client/remote/base.py sha256:8c6f1a77704a9e14 -->
<!-- helix:source tests/client/test_remote_http_server.py sha256:c036dc915ae2c6b7 -->

# Host the Tether HTTP API

`novirhiza-tether-web-api` is an optional FastAPI adapter around the public
`tether.client.ClientProtocol`. The application supplies authentication and a
provider that binds a client to the actor resolved for that request. The adapter
does not create a host, choose a database, or decide how a user authenticates.

## Install

The API adapter and client facade shown here are `0.3.0`
workspace artifacts, with an exact internal Python dependency cohort. Published
core `0.2.0` has an earlier client facade and is refused by this candidate API.
In this checkout, `uv sync --frozen` uses the local packages. The registry command
below applies after compatible releases contain these interfaces.

```sh
uv add novirhiza-tether novirhiza-tether-web-api
```

The API package currently pins FastAPI 0.162.0 and requires Python 3.11 or later.
Use the release compatibility matrix for the exact core/API pair you deploy.

## Compose the API

Create and own the `ClientHost` in your application lifespan. Implement
`Authenticator.actor_for()` using the host application's identity provider;
return the authenticated Tether `Actor` or raise
`ApiFailure(401, "authentication_required")` / `ApiFailure(403, "denied")`.
The example leaves that identity integration as an application seam:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from tether.client import Actor, ClientHost
from tether_web_api import ApiFailure, ApiSettings, create_api


class AppAuthenticator:
    async def actor_for(self, request: Request) -> Actor:
        user = await resolve_user_from_application_auth(request)
        if user is None:
            raise ApiFailure(401, "authentication_required")
        return Actor(id=user.subject, principal=user.display_name)


def build_app(host: ClientHost) -> FastAPI:
    api = create_api(
        clients=host.client,
        authenticator=AppAuthenticator(),
        settings=ApiSettings(api_prefix="/api/v1"),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await host.aclose()
            await host.wait_closed()

    app = FastAPI(lifespan=lifespan)
    app.mount("/assistant", api)
    return app
```

`resolve_user_from_application_auth()` represents your existing authentication
integration; it is intentionally not supplied by this package. When mounted at
`/assistant`, configure the adapter prefix as the path visible inside the mount
(for example `/api/v1`). Accepted result URLs include the mount root path.

The host and API should be created at application startup from explicit
`ClientSettings`. The `ClientProvider` receives an authenticated `Actor` and
returns its actor-bound client. Keep each user's authorization decision current;
the event endpoint rechecks identity and read authorization while streaming.
`max_request_bytes` defaults to 1 MiB. An outer ASGI boundary checks declared
content length and bounds streamed bodies before FastAPI parses them; requests
above the limit receive a content-free 413 problem before authentication or
client dispatch. This limit applies even when a caller omits `Content-Length`.
All body chunks share one absolute `request_timeout_seconds` deadline, whose
default is 30 seconds. A stalled body receives a content-free HTTP 408
`request_timeout` problem before authentication or command dispatch.

Unsafe methods accept an `Origin` that matches the direct ASGI request origin
or an exact value in `allowed_origins`. Wildcards, `null`, paths, userinfo, and
unlisted origins are rejected before authentication or command execution. A
missing Origin remains valid for non-browser bearer clients. The adapter never
uses forwarded headers to infer a public origin. Set `allowed_origins` when a
browser accesses the API through a TLS-terminating proxy or a different public
origin, such as Databricks Apps; keep that configured origin exact. CORS is
enabled only for the explicitly configured list.

The application remains responsible for cookie and CSRF policy in its own
authenticator. Do not pass an infrastructure/service actor for requests whose
ownership must belong to the caller. Do not log bearer values, request content,
or model payloads. Problems use content-free RFC 9457 details; expected
owner-hidden not-found errors map to 404. Unexpected exceptions remain visible
to the server's error reporting path.

## Versioned endpoints and schema

The default prefix is `/api/v1`. The API publishes the JSON Schema registry at
`/api/v1/schema/http-v1.json` and OpenAPI at `/api/v1/openapi.json`. OpenAPI
describes every public route and response. Use the schema registry for event
union definitions and exact counter fields; the source of truth is
`packages/web-api/schema/http-v1.json`.

The supported HTTP major is 1. A client configured for `/api/v2` against the
default v1 server receives an explicit 404 failure. The remote client preserves
that refusal and does not retry another API prefix.

The adapter maps the public client operations for capabilities, session listing,
lookup, snapshots, entries, history, pending confirmations, effects, call detail,
create/update, send/result, confirm, resume, cancellation, resolution, close,
restore, history links, and the event feed. A capability may report an operation
or archive tier unavailable; clients should inspect the capability response
instead of assuming that every backing profile implements every tier.

Turn sends require a stable `request_id`. A successful send returns HTTP 202 with
an accepted `turn_id`; use the result URL to poll until a terminal result appears.
The API does not make client disconnect cancel accepted work. Turn-specific
DELETE cancellation is distinct from session-wide cancellation. Confirmation
returns either an accepted turn handle or an explicit no-op state.

Events use Server-Sent Events (SSE). Advance a persisted cursor only after the
event marked `last_in_commit`; a commit can contain several events with one
sequence. `reply_text` is provisional Unicode text and does not advance the
durable cursor. `lagged` or `resync_required` requires a fresh snapshot and
replay. The `@tether/client` browser package implements this protocol for fetch
streams that need authorization headers.

## Security and operational boundaries

The API does not implement an identity provider, default authenticator, or
shared-user policy. Do not pass an infrastructure/service actor for requests
whose ownership must belong to the caller. Do not log bearer values, request
content, or model payloads. Problems use content-free RFC 9457 details; expected
owner-hidden not-found errors map to 404. Unexpected exceptions remain visible
to the server's error reporting path.

API request limits, queue sizes, event batch size, heartbeat and timeouts are
explicit `ApiSettings` values. A stream reauthorizes at delivery boundaries and
is closed on authentication or authorization failure. An application that
mounts the UI and API should use the same origin where practical and define its
cookie/CSRF policy in its own auth layer.

This guide establishes the package contract, not a Databricks Apps support claim.
The Databricks carrier's H2C ingress, OAuth scopes, database profile, restart
recovery and shutdown require the selected live platform proof recorded in the
web-platform acceptance evidence.
