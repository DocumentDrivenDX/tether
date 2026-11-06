import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { TetherError, type Change } from "@tether/client";
import { useTetherClient } from "./provider.js";
import { applyEvent, keys } from "./feed-reducer.js";

export interface ProvisionalReply { attemptId: string; turnId: string; text: string }
export type FeedState = "connecting" | "live" | "reconnecting" | "error" | "archived";
function provisional(previous: ProvisionalReply | null, event: Change): ProvisionalReply | null {
  if (event.type !== "reply_text") return null;
  const text = previous?.attemptId === event.attempt_id ? previous.text : "";
  if (BigInt(Array.from(text).length) !== event.offset) throw new TetherError("reply_offset_gap");
  return { attemptId: event.attempt_id, turnId: event.turn_id, text: text + event.text };
}

function requiresSnapshot(event: Change): boolean { return event.type === "resync_required" || event.type === "lagged"; }

function isOffsetGap(error: unknown): boolean {
  return error instanceof TetherError && error.code === "reply_offset_gap";
}

function waitForFeedEnd(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
    const timer = setTimeout(finish, 250);
    signal.addEventListener("abort", finish, { once: true });
  });
}

interface FeedReply { value: ProvisionalReply | null }
interface ConsumeResult { resync: boolean }

function applyFeedEvent(cache: ReturnType<typeof useQueryClient>, id: string, event: Change, reply: FeedReply): boolean {
  if (requiresSnapshot(event)) return true;
  if (event.type === "reply_text") {
    try { reply.value = provisional(reply.value, event); }
    catch (error: unknown) {
      if (isOffsetGap(error)) return true;
      throw error;
    }
  }
  if (event.type === "reply_reset") reply.value = null;
  if (event.type === "session_updated" && event.summary.status !== "running") reply.value = null;
  applyEvent(cache, id, event);
  return false;
}

async function consumeFeed(
  client: ReturnType<typeof useTetherClient>,
  cache: ReturnType<typeof useQueryClient>,
  id: string,
  seq: bigint,
  signal: AbortSignal,
  reply: FeedReply,
  setReply: (value: ProvisionalReply | null) => void,
  onReconnect: () => void,
  setState: (value: FeedState) => void,
): Promise<ConsumeResult> {
  for await (const event of client.follow({ sessionId: id, since: seq, signal, onReconnect })) {
    if (signal.aborted) return { resync: false };
    setState("live");
    if (applyFeedEvent(cache, id, event, reply)) {
      reply.value = null;
      setReply(null);
      return { resync: true };
    }
    if (event.type === "reply_text" || event.type === "reply_reset" || (event.type === "session_updated" && event.summary.status !== "running")) {
      setReply(reply.value);
    }
  }
  return { resync: false };
}

export function useSessionFeed(id: string | undefined, tier: "live" | "archive" = "live") {
  const client = useTetherClient();
  const cache = useQueryClient();
  const [state, setState] = useState<FeedState>("connecting");
  const [reply, setReply] = useState<ProvisionalReply | null>(null);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    if (!id || tier === "archive") {
      setState(tier === "archive" ? "archived" : "connecting");
      setReply(null);
      return;
    }
    const controller = new AbortController();
    const currentReply: FeedReply = { value: null };
    const clear = () => { currentReply.value = null; if (!controller.signal.aborted) setReply(null); };
    const run = async () => {
      setState("connecting"); clear();
      while (!controller.signal.aborted) {
        setState("connecting");
        const snapshot = await client.snapshot(id, { signal: controller.signal });
        if (controller.signal.aborted) return;
        cache.setQueryData(keys.snapshot(id, "live"), snapshot);
        const result = await consumeFeed(client, cache, id, snapshot.seq, controller.signal, currentReply, setReply,
          () => { clear(); setState("reconnecting"); }, setState);
        if (!result.resync && !controller.signal.aborted) await waitForFeedEnd(controller.signal);
      }
    };
    void run().catch((error: unknown) => {
      if (controller.signal.aborted) return;
      clear(); setState("error");
      if (error instanceof TetherError && (error.status === 401 || error.status === 403)) cache.clear();
    });
    return () => { controller.abort(); };
  }, [cache, client, id, tier, generation]);
  return { state, reply, reconnect: () => setGeneration((value) => value + 1) };
}
