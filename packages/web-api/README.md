# Tether Web API

This is an unreleased workspace artifact. The distribution name describes the
planned optional API package; use the repository workspace until a matching
release is published.

`novirhiza-tether-web-api` is the optional FastAPI adapter for the public
`tether.client.ClientProtocol`. It exports `create_api`, `ApiFactory`,
`ApiSettings`, `Authenticator`, `ApiFailure`, and the HTTP wire
`ClientCapabilities` value from `tether_web_api`.

The embedding application supplies a current-request `Authenticator` and a
`ClientProvider`. This package does not open storage, create a `ClientHost`,
choose credentials, or decide identity policy. Mount the returned ASGI app under
the app's lifespan owner and keep actor authorization current per request.

The HTTP schema registry is `tether_web_api/schema/http-v1.json` and is served
by the app at `/api/v1/schema/http-v1.json`. Versioned OpenAPI is served at
`/api/v1/openapi.json`. FastAPI is pinned to 0.162.0 in this package's current
metadata; use the release compatibility matrix for supported core/API pairs.

See the repository [HTTP API guide](../../docs/web-api.md) for composition,
identity, routes, and event handling. Core host setup is in
[Embed Tether](../../docs/embedding.md).
