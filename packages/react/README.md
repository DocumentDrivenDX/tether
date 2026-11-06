# `@tether/react`

This is an unreleased npm workspace package. The package name describes the
planned external artifact; use the repository workspace until a matching
release is published.

React integration for the public `@tether/client` package. It exports
`ClientProvider`, `useTetherClient`, the session/capabilities query hooks,
`useCommand`, and `useSessionFeed`.

```tsx
import { TetherClient } from "@tether/client";
import { ClientProvider, useSessions } from "@tether/react";

const client = new TetherClient({ baseUrl: "/api/v1" });

function SessionLinks() {
  const sessions = useSessions();
  if (sessions.isPending) return <p role="status">Loading…</p>;
  if (sessions.isError) return <p role="alert">Could not load sessions.</p>;
  return <ul>{sessions.data.items.map((item) => <li key={item.id}>{item.title}</li>)}</ul>;
}

export function App() {
  return <ClientProvider client={client}><SessionLinks /></ClientProvider>;
}
```

The provider owns its TanStack Query cache and clears it when the injected client
changes. Scope it to an authenticated identity; create a new provider/client
boundary when the active identity changes. Hooks use the injected client and do
not construct transport or identity.

The session feed reconciles committed events with the snapshot/query cache and
keeps attempt-scoped provisional text separate. A gap, lag marker, or
resynchronization request reloads a snapshot. Cleanup aborts its feed request and
does not close or cancel the borrowed client.

See the repository [React guide](../../docs/react.md) and
[HTTP API guide](../../docs/web-api.md).
