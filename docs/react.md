<!-- helix:publish audience=developer page=react kind=how-to voice=onboarding budget=1200 reviewed=2026-10-29/root/- -->
<!-- helix:source packages/client-ts/src/client.ts sha256:83e4b2a48d2a062d -->
<!-- helix:source packages/client-ts/src/feed.ts sha256:cd3fdcf40dec7466 -->
<!-- helix:source packages/client-ts/src/wire/counters.ts sha256:016342afe3c8a0ba -->
<!-- helix:source packages/client-ts/src/wire/validators.ts sha256:847ba1331d07c170 -->
<!-- helix:source packages/react/src/provider.tsx sha256:5f4ce353910bfd84 -->
<!-- helix:source packages/react/src/use-feed.ts sha256:807a4d6486ad38fb -->
<!-- helix:source packages/ui/src/actions.tsx sha256:73cbcca94c6c9533 -->
<!-- helix:source packages/ui/src/index.tsx sha256:a32958187270fccc -->
<!-- helix:source packages/ui/src/navigation.tsx sha256:29392320ab314da9 -->
<!-- helix:source packages/ui/src/presentation.tsx sha256:27460f96409d4c2d -->
<!-- helix:source packages/ui/src/session.tsx sha256:708dddf8a16613e9 -->
<!-- helix:source apps/reference-web/src/main.tsx sha256:5258383df66d37e2 -->
<!-- helix:source packages/react/package.json sha256:252b7abfc1a813b2 -->
<!-- helix:source packages/ui/package.json sha256:17204d700a96b143 -->
<!-- helix:source tests/ui/reference.spec.ts sha256:2b1412d0b6bd29ba -->

# Build a Tether React interface

The optional browser packages separate HTTP transport, React state, and visual
components. `@tether/client` has no React dependency. `@tether/react` supplies the
client provider, TanStack Query hooks, and session feed. `@tether/ui` supplies
replaceable session components and opt-in style tokens. Your application owns
authentication, routing, and composition.

## Install the packages

The browser packages are currently unreleased workspace artifacts. In this
checkout, install the locked workspace dependencies from the repository root
and build them:

```sh
npm ci
npm run build
```

After release, install `@tether/client`, `@tether/react`, and `@tether/ui` with
the application's React dependencies. This workspace tests React 19.3.1; check
the release compatibility matrix before selecting another version.

```sh
npm install @tether/client @tether/react @tether/ui react react-dom
```

## Create an authenticated client

`TetherClient` receives a base URL and optional asynchronous headers callback.
The callback runs for each request, including feed reconnects, so it can read the
current application credential rather than a token captured when the page first
loaded.

```tsx
import { TetherClient } from "@tether/client";
import { ClientProvider } from "@tether/react";
import { SessionWorkspace } from "@tether/ui";
import "@tether/ui/styles.css";

const client = new TetherClient({
  baseUrl: "/api/v1",
  headers: async () => {
    const token = await auth.currentAccessToken();
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return headers;
  },
});

interface ConversationsProps {
  selectedId?: string;
  selectedTier?: "live" | "archive";
  navigate(path: string): void;
}

export function Conversations({ selectedId, selectedTier = "live", navigate }: ConversationsProps) {
  return (
    <ClientProvider client={client}>
      <SessionWorkspace
        {...(selectedId === undefined ? {} : { selectedId })}
        selectedTier={selectedTier}
        onSelect={(id, tier) => navigate(`/sessions/${encodeURIComponent(id)}?tier=${tier}`)}
      />
    </ClientProvider>
  );
}
```

`auth.currentAccessToken()` and `navigate()` stand for the application's own
authentication and router. Keep a provider boundary scoped to the authenticated
identity. Replacing its client clears that provider's cached server state. Never
persist access tokens in browser storage through the Tether client.

The client validates incoming payloads against generated schemas. It exposes
Tether counters as `bigint` while preserving arbitrary user JSON values as
provided. Methods include session reads and writes, history, effects,
capabilities, result polling, turn-specific cancellation, and reconnecting
`follow()`. Pass an `AbortSignal` in read/command options when the caller owns
cancellation. Closing a feed aborts that feed; it does not cancel accepted work
or dispose a borrowed client.

## Use hooks without the reference components

`@tether/react` exports `useTetherClient`, query hooks (`useCapabilities`,
`useSessions`, `useSession`, `useHistory`, `usePending`, `useEffects`),
`useCommand`, and `useSessionFeed`. A custom screen can use the same client and
cache semantics:

```tsx
import { useCommand, useSession, useSessionFeed, useTetherClient } from "@tether/react";

function SessionScreen({ id }: { id: string }) {
  const client = useTetherClient();
  const session = useSession(id);
  const feed = useSessionFeed(id);
  const command = useCommand(id);
  if (session.isPending) return <p role="status">Opening session…</p>;
  if (session.isError) return <p role="alert">Session could not be loaded.</p>;
  return (
    <section>
      <h1>{session.data.view.summary.title ?? "Untitled session"}</h1>
      <p role="status">{feed.state}</p>
      <button disabled={command.isPending} onClick={() => command.mutate(() => client.resume(id))}>Resume</button>
      {command.isError && <p role="alert">The session could not be resumed.</p>}
    </section>
  );
}
```

The feed uses snapshot and committed events to keep cached session state current.
Provisional text is an attempt-scoped view, not saved conversation history.
Reconnect, lag and commit boundaries are handled by the transport and hook;
applications should still provide visible loading, error, and reconnect states.

## Replace presentation slots

`SessionWorkspace` accepts `selectedId`, `selectedTier`, `onSelect`, and an optional `slots`
object. Its `slots.Layout` replaces the navigation/content layout, and
`slots.Message` replaces message presentation. `onSelect(id, tier)` lets the
application preserve live/archive selection in its route and switch to live
after a successful restore. Archive views read archive snapshot/history and
hide live commands and feeds. `SessionView` accepts an `id`, an `onSelect`
callback, and optional `tier` and `messageRenderer`; it has no layout slot. The styles are optional.
Import the tokens only when the reference visual baseline is useful; the
components do not require Tailwind. Use semantic buttons, labels,
status/error announcements, visible focus and keyboard navigation in
replacements. Keep selected-session routing in your app and make the active
location perceivable to assistive technology.

Both slots have exported prop types, so replacements remain checked against the
same navigation/content and message values:

```tsx
import {
  SessionWorkspace,
  type MessageRendererProps,
  type SessionLayoutProps,
} from "@tether/ui";

function ProjectLayout({ navigation, content }: SessionLayoutProps) {
  return <div className="project-workspace"><nav>{navigation}</nav><main id="tether-main">{content}</main></div>;
}

function ProjectMessage({ message }: MessageRendererProps) {
  return <article><h3>{message.role}</h3><p>{message.content}</p></article>;
}

<SessionWorkspace
  selectedId={selectedId}
  onSelect={(id, tier) => navigate(`/sessions/${encodeURIComponent(id)}?tier=${tier}`)}
  slots={{ Layout: ProjectLayout, Message: ProjectMessage }}
/>
```

## Package boundaries

Import only from public package entry points. `@tether/client` owns the typed
HTTP/SSE client, `@tether/react` owns query/feed integration, and `@tether/ui`
owns components. The reference application composes them. No browser package
constructs server-side authority or persists session state as a separate source
of truth.

For the HTTP identity and deployment boundary, see [Host the Tether HTTP API](web-api.md).
For public Python composition, see [Embed Tether](embedding.md). Browser behavior
and accessibility claims are qualified by the exact package and real-browser
acceptance evidence in `docs/helix/03-test/web-platform-test-plan.md`.
