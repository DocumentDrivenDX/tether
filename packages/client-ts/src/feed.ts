import type { Change, FollowOptions } from "./types.js";
import { decode } from "./wire/validators.js";
import { TetherError } from "./errors.js";
import { readSse, type SseFrame } from "./sse.js";
import { Transport } from "./transport.js";
import { wait } from "./wait.js";

class CommitBuffer {
  private events: Change[] = [];
  private sequence: bigint | undefined;
  private bytes = 0;
  accept(frame: SseFrame): Change[] {
    const event = decode("Change", JSON.parse(frame.data) as unknown);
    this._checkEventName(frame, event);
    if (!("seq" in event)) return this._ephemeral(frame, event);
    this._append(frame, event);
    return event.last_in_commit ? this._commit(frame, event) : this._intermediate(frame);
  }
  private _checkEventName(frame: SseFrame, event: Change): void {
    if (frame.event !== "message" && frame.event !== event.type) throw new TetherError("event_type_mismatch");
  }
  private _ephemeral(frame: SseFrame, event: Change): Change[] {
    if (frame.id !== undefined) throw new TetherError("ephemeral_event_id");
    return [event];
  }
  private _append(frame: SseFrame, event: Change & { seq: bigint }): void {
    if (this.sequence !== undefined && this.sequence !== event.seq) throw new TetherError("incomplete_commit");
    this.sequence = event.seq;
    this.bytes += new TextEncoder().encode(frame.data).byteLength;
    if (this.events.length >= 256 || this.bytes > 4 * 1024 * 1024) throw new TetherError("commit_too_large");
    this.events.push(event);
  }
  private _intermediate(frame: SseFrame): Change[] {
    if (frame.id !== undefined) throw new TetherError("intermediate_event_id");
    return [];
  }
  private _commit(frame: SseFrame, event: Change & { seq: bigint }): Change[] {
    if (frame.id !== event.seq.toString()) throw new TetherError("commit_id_mismatch");
    const completed = this.events;
    this.events = []; this.bytes = 0; this.sequence = undefined;
    return completed;
  }
}

async function* connection(transport: Transport, options: FollowOptions, since?: bigint): AsyncGenerator<Change> {
  const query: Record<string, string> = {};
  if (options.sessionId !== undefined) query.session_id = options.sessionId;
  if (since !== undefined) query.since = since.toString();
  const response = await transport.open("GET", "events", { query, signal: options.signal }, true);
  if (!response.ok) throw new TetherError(response.status === 401 ? "authentication_required" : "stream_denied", { status: response.status, retryable: response.status >= 500 });
  if (!response.body || !response.headers.get("content-type")?.startsWith("text/event-stream")) throw new TetherError("invalid_stream_response");
  const commits = new CommitBuffer();
  for await (const frame of readSse(response.body, { signal: options.signal })) {
    for (const event of commits.accept(frame)) yield event;
  }
}

interface FeedState { cursor: bigint | undefined; retries: number; done: boolean }

export async function* follow(transport: Transport, options: FollowOptions = {}): AsyncGenerator<Change> {
  yield* reconnect(transport, options);
}

async function* reconnect(transport: Transport, options: FollowOptions): AsyncGenerator<Change> {
  const state: FeedState = { cursor: options.since, retries: 0, done: false };
  while (!options.signal?.aborted && !state.done) {
    yield* attempt(transport, options, state);
    if (state.done || options.signal?.aborted) return;
    if (++state.retries > 5) throw new TetherError("stream_unavailable", { retryable: true });
    options.onReconnect?.();
    await wait(Math.min(250 * 2 ** state.retries, 5000), options.signal);
  }
}

async function* attempt(
  transport: Transport, options: FollowOptions, state: FeedState,
): AsyncGenerator<Change> {
  try {
    for await (const event of connection(transport, options, state.cursor)) {
      yield event;
      if (advancesCursor(event)) { state.cursor = event.seq; state.retries = 0; }
      if (event.type === "resync_required") { state.done = true; return; }
    }
  } catch (error) {
    if (options.signal?.aborted) { state.done = true; return; }
    if (isFatal(error)) throw error;
  }
}

function advancesCursor(event: Change): event is Change & { seq: bigint; last_in_commit: true } {
  return "seq" in event && event.last_in_commit;
}

function isFatal(error: unknown): boolean {
  return error instanceof TetherError && !error.retryable;
}
