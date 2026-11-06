import type { Change, SessionSnapshot } from "@tether/client";
import type { QueryClient } from "@tanstack/react-query";

export const keys = {
  sessions: ["sessions"] as const,
  snapshot: (id: string, tier: "live" | "archive" = "live") => ["snapshot", id, tier] as const,
  history: (id: string, tier: "live" | "archive" = "live") => ["history", id, tier] as const,
  pending: (id: string, tier: "live" | "archive" = "live") => ["pending", id, tier] as const,
  effects: (id: string, tier: "live" | "archive" = "live") => ["effects", id, tier] as const,
};
export function applyEvent(cache: QueryClient, id: string, event: Change): void {
  if (!("seq" in event)) return;
  cache.setQueryData<SessionSnapshot>(keys.snapshot(id), (current) => {
    if (!current || event.seq < current.seq) return current;
    if (event.type === "session_updated" && event.summary.id === id) {
      return { ...current, view: { ...current.view, summary: event.summary }, seq: event.last_in_commit ? event.seq : current.seq };
    }
    if (event.type === "entries_appended" && event.session_id === id) {
      const head = event.entries.reduce((value, entry) => entry.index > value ? entry.index : value, current.through_index);
      return { ...current, through_index: head, seq: event.last_in_commit ? event.seq : current.seq };
    }
    return current;
  });
  if (event.type === "session_removed" && event.session_id === id) cache.removeQueries({ queryKey: keys.snapshot(id) });
  if (event.last_in_commit) {
    for (const key of [keys.sessions, keys.history(id), keys.pending(id), keys.effects(id)]) {
      void cache.invalidateQueries({ queryKey: key });
    }
  }
}
