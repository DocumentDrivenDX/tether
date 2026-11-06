<!-- helix:publish audience=developer page=remote-terminal kind=how-to voice=onboarding budget=1200 reviewed=2026-10-29/root/- -->
<!-- helix:source src/tether/cli/command.py sha256:4f92601f2c24b7b1 -->
<!-- helix:source src/tether/client/remote/client.py sha256:8af4dc75eb573830 -->
<!-- helix:source src/tether/client/remote/base.py sha256:8c6f1a77704a9e14 -->
<!-- helix:source src/tether/client/remote/errors.py sha256:783cee13e57ebebf -->
<!-- helix:source src/tether/client/remote/reads.py sha256:cbb036edf7a108ba -->
<!-- helix:source src/tether/client/remote/settings.py sha256:e56db31559cca859 -->
<!-- helix:source src/tether/client/remote/wire.py sha256:05d06907532e4db7 -->
<!-- helix:source src/tether/client/databricks.py sha256:7249f76ab1371cba -->
<!-- helix:source src/tether/cli/remote.py sha256:084a0a077baa9228 -->
<!-- helix:source tests/test_remote_service_caller.py sha256:506e530e983c840e -->
<!-- helix:source tests/test_remote_terminal.py sha256:494318c13cd635ec -->
<!-- helix:source src/tether/tui/__init__.py sha256:b84ee5b45540d537 -->
<!-- helix:source src/tether/tui/feed.py sha256:336bda88b592bd50 -->
<!-- helix:source tests/test_tui_feed_recovery.py sha256:9b50b740190610ec -->
<!-- helix:source tests/test_tui_feed_presentation.py sha256:5d4338ce3d7d41f9 -->

# Use the remote Tether terminal

The optional Python remote client lets the reference terminal connect to an
existing Tether HTTP API. The endpoint and Databricks CLI profile are selected
explicitly for each command; the client does not discover an endpoint or fall
back to local storage.

## Install

The remote client and CLI profile flags target the unreleased `0.2.0.dev0`
candidate. Published core `0.2.0` is a different, unqualified client cohort.
From this checkout, run with the locked environment and explicit extras:

```sh
uv run --frozen --extra tui --extra remote tether --help
```

After a release includes the remote profile, the installed-command recipe is:

```sh
uv tool install 'novirhiza-tether[tui,remote]'
```

The `remote` extra installs HTTPX. The `tui` extra installs Textual. A Databricks
CLI installation with an already authenticated named profile is required for
this profile-specific authentication path.

## Launch the remote TUI

```sh
tether --endpoint https://tether.example.test \
  --remote-profile my-databricks-profile
```

For a non-default API mount prefix:

```sh
tether --endpoint https://tether.example.test \
  --remote-profile my-databricks-profile \
  --api-prefix /assistant/api/v1
```

The endpoint must be an HTTPS origin; HTTP is accepted only for explicit
loopback development. The CLI profile must exist and be authenticated. A remote
command refuses local model, root, volatile storage, and replay options so it
cannot silently create a second local composition.

For a Databricks Apps service configured to forward the caller's OAuth identity,
opt in with `--remote-service-caller` alongside the endpoint and named profile:

```sh
tether --endpoint https://tether.example.test \
  --remote-profile my-databricks-profile \
  --remote-service-caller
```

This mode projects the profile's fresh `Authorization` value into
`X-Tether-Service-Caller-Authorization` for the selected carrier's explicitly
configured service-caller resolver. Use it only after verifying that the Apps
ingress supplies the expected caller credential and the server enables that
resolver; the generic API does not trust this header by default. The option does
not accept a token value and does not change behavior when omitted. Protect the
Databricks CLI profile and token cache as usual.

The same flags can be combined with supported actions:

```sh
tether --endpoint https://tether.example.test \
  --remote-profile my-databricks-profile list

tether --endpoint https://tether.example.test \
  --remote-profile my-databricks-profile show SESSION_ID
```

Current profile behavior is implemented for the Databricks CLI OAuth profile.
The CLI resolves the selected profile and obtains credentials when making each
HTTP request/reconnect. Keep the profile file and token cache private. The CLI
does not print credentials; do not enable shell tracing around authentication
commands.

## Ownership and limits

The terminal borrows the remote `ClientProtocol`. Its launcher owns and closes
the HTTP transport when the UI exits. Closing the terminal does not cancel an
accepted turn. To cancel an identified turn from application code, call
`await remote.cancel_turn(session_id, turn_id)` on the public remote client.
Reconcile a lost response through `turn_result` before issuing a new request.

An injected HTTP transport must support a long-lived SSE subscription alongside
concurrent reads and commands. A single HTTP/1.1 connection qualifies release
after the subscription closes; it does not support the concurrent TUI profile.
HTTP/2 multiplexing can provide concurrency without separate TCP connections.

After a feed gap or lag, the terminal closes the old subscription, reloads an
atomic snapshot and committed history, and reconnects after that snapshot's
watermark. A failed feed or exhausted recovery displays a fixed live-updates
warning; reopen the client to reconnect. Feed recovery never repeats a command.

The remote client requires the versioned API from `novirhiza-tether-web-api`.
The server must authenticate every request and authorize session, history,
effect, and model-call detail reads for the current actor. Databricks Apps and
Lakebase compatibility still require evidence for the exact runner, ingress,
database role profile, and platform versions; this client guide does not claim
those deployment profiles are qualified.

For in-process Python hosts, see [Embed Tether](embedding.md). For HTTP adapter
composition, routes, and schema, see [Host the Tether HTTP API](web-api.md).
