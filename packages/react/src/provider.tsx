import { createContext, useContext, useEffect, useMemo, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { SessionClient } from "@tether/client";

const Context = createContext<SessionClient | null>(null);
export function ClientProvider({ client, children }: { client: SessionClient; children: ReactNode }) {
  const identity = useMemo(() => ({
    client,
    cache: new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5000 } } }),
    attachments: { count: 0 },
  }), [client]);
  const cache = identity.cache;
  useEffect(() => {
    identity.attachments.count += 1;
    return () => {
      queueMicrotask(() => {
        identity.attachments.count -= 1;
        if (identity.attachments.count === 0) cache.clear();
      });
    };
  }, [cache, identity]);
  return <Context.Provider value={identity.client}><QueryClientProvider client={cache}>{children}</QueryClientProvider></Context.Provider>;
}
export function useTetherClient(): SessionClient {
  const client = useContext(Context);
  if (!client) throw new Error("Tether ClientProvider is required");
  return client;
}
