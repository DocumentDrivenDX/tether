import { useQuery, useMutation, useQueryClient, type UseQueryResult, type UseMutationResult } from "@tanstack/react-query";
import type { Capabilities, ListOptions, SessionPage, SessionSnapshot, HistoryPage, PendingConfirmation, EffectPage } from "@tether/client";
import { useTetherClient } from "./provider.js";
import { keys } from "./feed-reducer.js";

export function useSessions(options: ListOptions = {}): UseQueryResult<SessionPage, Error> {
  const client = useTetherClient();
  return useQuery({ queryKey: [...keys.sessions, options], queryFn: ({ signal }) => client.list({ ...options, signal }) });
}
export type SessionTier = "live" | "archive";
export function useSession(id: string, tier: SessionTier = "live"): UseQueryResult<SessionSnapshot, Error> {
  const client = useTetherClient();
  return useQuery({ queryKey: keys.snapshot(id, tier), queryFn: ({ signal }) => client.snapshot(id, { signal, tier }) });
}
export function useHistory(id: string, start: bigint, tier: SessionTier = "live"): UseQueryResult<HistoryPage, Error> {
  const client = useTetherClient();
  return useQuery({ queryKey: [...keys.history(id, tier), start.toString()], queryFn: ({ signal }) => client.history(id, { start, limit: 100, signal, tier }) });
}
export function usePending(id: string, tier: SessionTier = "live"): UseQueryResult<PendingConfirmation | null, Error> {
  const client = useTetherClient();
  return useQuery({ queryKey: keys.pending(id, tier), queryFn: ({ signal }) => client.pending(id, { signal, tier }) });
}
export function useEffects(id: string, tier: SessionTier = "live"): UseQueryResult<EffectPage, Error> {
  const client = useTetherClient();
  return useQuery({ queryKey: keys.effects(id, tier), queryFn: ({ signal }) => client.effects(id, { signal, tier }) });
}
export function useCommand(id: string): UseMutationResult<unknown, Error, () => Promise<unknown>> {
  const client = useTetherClient();
  const cache = useQueryClient();
  return useMutation({ mutationFn: (command: () => Promise<unknown>) => command(),
    onSettled: async () => {
      await Promise.all([keys.snapshot(id), keys.pending(id), keys.effects(id), keys.history(id), keys.sessions].map((queryKey) => cache.invalidateQueries({ queryKey })));
    }, meta: { client },
  });
}

export function useCapabilities(): UseQueryResult<Capabilities, Error> {
  const client = useTetherClient();
  return useQuery({ queryKey: ["capabilities"], queryFn: ({ signal }) => client.capabilities({ signal }) });
}
