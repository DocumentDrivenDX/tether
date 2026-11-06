# `@tether/ui`

This is an unreleased npm workspace package. The package name describes the
planned external artifact; use the repository workspace until a matching
release is published.

Composable React components for a Tether session workspace. The package exports
`SessionWorkspace`, `SessionList`, `SessionView`, `Composer`, `Confirmation`,
`UnknownEffects`, `DefaultLayout`, and `DefaultMessage`.

```tsx
import { ClientProvider } from "@tether/react";
import { SessionWorkspace } from "@tether/ui";
import "@tether/ui/styles.css";

<ClientProvider client={client}>
  <SessionWorkspace selectedId={sessionId} onSelect={setSessionId} />
</ClientProvider>
```

The stylesheet is optional. `SessionWorkspace` accepts layout and message
renderer slots through its `slots` prop; session selection and route ownership
remain in the application. Replace components while preserving accessible names,
keyboard operation, status/error announcements, and visible focus.

See the repository [React guide](../../docs/react.md) for composition examples
and the package boundaries.
