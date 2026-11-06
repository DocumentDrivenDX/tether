import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { useQueryClient } from "@tanstack/react-query";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Change, SessionClient, SessionSnapshot } from "@tether/client";
import { ClientProvider, useSessionFeed } from "../src/index.js";

afterEach(cleanup);

class EventStream implements AsyncIterable<Change> {
  private pending: Change[] = [];
  private waiters: Array<(result: IteratorResult<Change>) => void> = [];
  private ended = false;

  push(event: Change): void {
    const waiter = this.waiters.shift();
    if (waiter) waiter({ done: false, value: event });
    else this.pending.push(event);
  }

  end(): void {
    this.ended = true;
    for (const waiter of this.waiters.splice(0)) waiter({ done: true, value: undefined });
  }

  [Symbol.asyncIterator](): AsyncIterator<Change> {
    return { next: () => this.next() };
  }

  private next(): Promise<IteratorResult<Change>> {
    const event = this.pending.shift();
    if (event) return Promise.resolve({ done: false, value: event });
    if (this.ended) return Promise.resolve({ done: true, value: undefined });
    return new Promise((resolve) => this.waiters.push(resolve));
  }
}

function snapshot(owner: string, seq = 0n): SessionSnapshot {
  return {
    seq,
    through_index: -1n,
    view: {
      summary: { id: "s1", owner, created_at: "now", updated_at: "now", status: "running" },
      settings: {}, specs: {}, tools: [], tools_status: "not_loaded",
    },
  };
}

function client(owner: string) {
  const streams: EventStream[] = [];
  const signals: AbortSignal[] = [];
  const reconnects: Array<() => void> = [];
  const api = {
    snapshot: vi.fn(async () => snapshot(owner)),
    follow: vi.fn((options: { signal?: AbortSignal; onReconnect?: () => void }) => {
      const stream = new EventStream();
      streams.push(stream);
      if (options.signal) signals.push(options.signal);
      if (options.onReconnect) reconnects.push(options.onReconnect);
      return stream;
    }),
    cancel: vi.fn(async () => true),
  };
  return { api: api as unknown as SessionClient, streams, signals, reconnects };
}

function FeedProbe({ id = "s1", onCache }: { id?: string; onCache?: (cache: ReturnType<typeof useQueryClient>) => void }) {
  const feed = useSessionFeed(id);
  const cache = useQueryClient();
  onCache?.(cache);
  return <output data-testid="feed">{feed.state}:{feed.reply?.text ?? ""}</output>;
}

describe("useSessionFeed lifecycle and cache reconciliation", () => {
  it("clears the previous identity cache during the provider switch", async () => {
    const first = client("alice");
    const second = client("bob");
    let oldCache: ReturnType<typeof useQueryClient> | undefined;
    const view = render(<ClientProvider client={first.api}><FeedProbe onCache={(cache) => { oldCache = cache; }} /></ClientProvider>);
    await waitFor(() => expect(oldCache?.getQueryData(["snapshot", "s1", "live"])).toMatchObject({ view: { summary: { owner: "alice" } } }));

    view.rerender(<ClientProvider client={second.api}><FeedProbe /></ClientProvider>);
    await waitFor(() => expect(screen.getByTestId("feed")).toHaveTextContent("connecting:"));
    expect(oldCache?.getQueryData(["snapshot", "s1", "live"])).toBeUndefined();
    expect(second.api.snapshot).toHaveBeenCalledOnce();
  });

  it("keeps the active identity cache through StrictMode effect replay", async () => {
    const host = client("alice");
    let cache: ReturnType<typeof useQueryClient> | undefined;
    render(<StrictMode><ClientProvider client={host.api}><FeedProbe onCache={(value) => { cache = value; }} /></ClientProvider></StrictMode>);
    await waitFor(() => expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.seq).toBe(0n));

    await act(async () => host.streams[0]?.push({
      type: "session_updated",
      seq: 1n,
      last_in_commit: true,
      summary: { id: "s1", owner: "alice", created_at: "now", updated_at: "fresh" },
    } as Change));

    await waitFor(() => expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.seq).toBe(1n));
    expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.view.summary.updated_at).toBe("fresh");
  });

  it("publishes exact counters only at the final event of a commit", async () => {
    const host = client("alice");
    let cache: ReturnType<typeof useQueryClient> | undefined;
    render(<ClientProvider client={host.api}><FeedProbe onCache={(value) => { cache = value; }} /></ClientProvider>);
    await waitFor(() => expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.seq).toBe(0n));
    const stream = host.streams[0];

    await act(async () => stream?.push({ type: "session_updated", seq: 9007199254740992n, last_in_commit: false, summary: { id: "s1", owner: "alice", created_at: "now", updated_at: "later" } } as Change));
    expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.seq).toBe(0n);
    await act(async () => stream?.push({ type: "session_updated", seq: 9007199254740993n, last_in_commit: true, summary: { id: "s1", owner: "alice", created_at: "now", updated_at: "later" } } as Change));
    await waitFor(() => expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.seq).toBe(9007199254740993n));
  });

  it("counts Unicode code points, clears a gap, and reloads a snapshot", async () => {
    const host = client("alice");
    render(<ClientProvider client={host.api}><FeedProbe /></ClientProvider>);
    await waitFor(() => expect(host.streams).toHaveLength(1));
    await act(async () => host.streams[0]?.push({ type: "reply_text", session_id: "s1", turn_id: "t1", attempt_id: "a1", offset: 0n, text: "🙂" } as Change));
    await act(async () => host.streams[0]?.push({ type: "reply_text", session_id: "s1", turn_id: "t1", attempt_id: "a1", offset: 1n, text: "é" } as Change));
    await waitFor(() => expect(screen.getByTestId("feed")).toHaveTextContent("live:🙂é"));

    await act(async () => host.streams[0]?.push({ type: "reply_text", session_id: "s1", turn_id: "t1", attempt_id: "a1", offset: 4n, text: "lost" } as Change));
    await waitFor(() => expect(host.api.snapshot).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("feed")).toHaveTextContent("connecting:");
  });

  it.each([
    { type: "lagged", missed: 2n },
    { type: "resync_required", reason: "gap" },
  ] as const)("clears reply text and resynchronizes $type feeds", async (event) => {
    const host = client("alice");
    render(<ClientProvider client={host.api}><FeedProbe /></ClientProvider>);
    await waitFor(() => expect(host.streams).toHaveLength(1));
    await act(async () => host.streams[0]?.push({ type: "reply_text", session_id: "s1", turn_id: "t1", attempt_id: "a1", offset: 0n, text: "partial" } as Change));
    await waitFor(() => expect(screen.getByTestId("feed")).toHaveTextContent("live:partial"));
    act(() => host.reconnects[0]?.());
    await waitFor(() => expect(screen.getByTestId("feed")).toHaveTextContent("reconnecting:"));
    await act(async () => host.streams[0]?.push(event as Change));
    await waitFor(() => expect(host.api.snapshot).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("feed")).toHaveTextContent("connecting:");
  });

  it("replaces a snapshot after a feed closes in the middle of a commit", async () => {
    const host = client("alice");
    let cache: ReturnType<typeof useQueryClient> | undefined;
    render(<ClientProvider client={host.api}><FeedProbe onCache={(value) => { cache = value; }} /></ClientProvider>);
    await waitFor(() => expect(host.streams).toHaveLength(1));
    await act(async () => host.streams[0]?.push({ type: "session_updated", seq: 8n, last_in_commit: false, summary: { id: "s1", owner: "alice", created_at: "now", updated_at: "partial" } } as Change));
    expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.seq).toBe(0n);
    await act(async () => host.streams[0]?.end());
    await waitFor(() => expect(host.api.snapshot).toHaveBeenCalledTimes(2));
    expect(cache?.getQueryData<SessionSnapshot>(["snapshot", "s1", "live"])?.seq).toBe(0n);
  });

  it("aborts the borrowed stream without cancelling its session", async () => {
    const host = client("alice");
    const view = render(<ClientProvider client={host.api}><FeedProbe /></ClientProvider>);
    await waitFor(() => expect(host.signals).toHaveLength(1));
    const signal = host.signals[0];
    view.unmount();
    expect(signal?.aborted).toBe(true);
    expect(host.api.cancel).not.toHaveBeenCalled();
  });

  it.each([401, 403])("clears protected cache after authorization status %i", async (status) => {
    const host = client("alice");
    host.api.snapshot.mockRejectedValueOnce(new (await import("@tether/client")).TetherError("authorization_revoked", { status }));
    let cache: ReturnType<typeof useQueryClient> | undefined;
    render(<ClientProvider client={host.api}><FeedProbe onCache={(value) => { cache = value; }} /></ClientProvider>);
    await waitFor(() => expect(screen.getByTestId("feed")).toHaveTextContent("error:"));
    expect(cache?.getQueryCache().getAll()).toHaveLength(0);
  });

  it("backs off after an empty feed close instead of spinning snapshots", async () => {
    const host = client("alice");
    host.api.follow.mockImplementation(() => {
      const stream = new EventStream();
      stream.end();
      return stream;
    });
    vi.useFakeTimers();
    try {
      const view = render(<ClientProvider client={host.api}><FeedProbe /></ClientProvider>);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(host.api.snapshot).toHaveBeenCalledOnce();
      await act(async () => { await vi.advanceTimersByTimeAsync(249); });
      expect(host.api.snapshot).toHaveBeenCalledOnce();
      await act(async () => { await vi.advanceTimersByTimeAsync(1); });
      expect(host.api.snapshot).toHaveBeenCalledTimes(2);
      view.unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
