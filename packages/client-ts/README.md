# `@tether/client`

This is an unreleased npm workspace package. The package name describes the
planned external artifact; use the repository workspace until a matching
release is published.

The React-free TypeScript client for Tether's versioned HTTP API. It provides
typed session commands and reads, runtime response validation, and a reconnecting
SSE feed. Import from the package root; internal paths are not public.

```ts
import { TetherClient } from "@tether/client";

const client = new TetherClient({
  baseUrl: "/api/v1",
  headers: async () => {
    const token = await auth.currentAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  },
});

const page = await client.list({ limit: 50 });
for (const session of page.items) console.log(session.id, session.entry_count);
```

The asynchronous `headers` callback runs on each HTTP request and feed
reconnection so the application can supply current credentials. Never store
tokens in Tether session settings. `TransportSettings` also accepts an injected
fetch implementation, request/response bounds, and fetch credentials mode.

Tether's arbitrary-size counters are decoded as `bigint`; arbitrary user JSON
settings, arguments, facts, and metadata remain their original JSON values.
`follow()` emits only committed durable event batches while keeping provisional
reply events separate from durable cursor movement. Abort signals stop the
caller-owned request/feed and do not cancel an accepted turn.

Read the [React guide](../../docs/react.md) for hooks and components, and the
[HTTP API guide](../../docs/web-api.md) for the wire contract and auth boundary.
