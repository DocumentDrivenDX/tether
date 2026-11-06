import type { TetherWire } from "./wire/generated.js";
import { decode } from "./wire/validators.js";
import { TetherError } from "./errors.js";
import { Transport, type RequestOptions, type TransportSettings } from "./transport.js";
import { follow } from "./feed.js";
import { wait } from "./wait.js";
import type { Exact } from "./exact.js";
import type { AcceptedTurn, CallDetailRequest, ConfirmRequest, CreateSessionRequest, FollowOptions,
  HistoryLinkRequest, ListOptions, PageOptions, ReadOptions, ResolveRequest, SendRequest,
  SessionClient, TurnHandle, TurnResult, UpdateSessionRequest } from "./types.js";

function path(id: string, suffix = ""): string { return `sessions/${encodeURIComponent(id)}${suffix}`; }
function query(options: ReadOptions | PageOptions | ListOptions = {}): Record<string, string> {
  return Object.fromEntries(Object.entries(options).filter(([key, value]) => key !== "signal" && value !== undefined).map(([key, value]) => [key, String(value)]));
}
function request(options: ReadOptions | PageOptions | ListOptions = {}, body?: unknown): RequestOptions {
  return { query: query(options), signal: options.signal, body };
}

export class TetherClient implements SessionClient {
  private readonly transport: Transport;
  constructor(settings: TransportSettings) { this.transport = new Transport(settings); }
  private async read<K extends keyof TetherWire>(schema: K, resource: string, options?: ReadOptions | PageOptions | ListOptions): Promise<Exact<TetherWire[K]>> {
    return decode(schema, await this.transport.request("GET", resource, request(options)));
  }
  private async write<K extends keyof TetherWire>(schema: K, method: string, resource: string, body?: unknown, options?: ReadOptions): Promise<Exact<TetherWire[K]>> {
    return decode(schema, await this.transport.request(method, resource, request(options, body)));
  }
  capabilities(options?: ReadOptions) { return this.read("ClientCapabilities", "capabilities", options); }
  list(options?: ListOptions) { return this.read("PageSessionSummary", "sessions", options); }
  get(id: string, options?: ReadOptions) { return this.read("SessionView", path(id), options); }
  snapshot(id: string, options?: ReadOptions) { return this.read("SessionSnapshot", path(id, "/snapshot"), options); }
  entries(id: string, options?: PageOptions) { return this.read("EntryPage", path(id, "/entries"), options); }
  history(id: string, options?: PageOptions) { return this.read("HistoryPage", path(id, "/history"), options); }
  effects(id: string, options?: PageOptions) { return this.read("EffectPage", path(id, "/effects"), options); }
  async find(ref: string, options?: ReadOptions) {
    const value = await this.transport.request("GET", `sessions/by-ref/${encodeURIComponent(ref)}`, request(options));
    return value === null ? null : decode("SessionSummary", value);
  }
  async pending(id: string, options?: ReadOptions) {
    const value = await this.transport.request("GET", path(id, "/pending"), request(options));
    return value === null ? null : decode("PendingConfirmation", value);
  }
  async callDetail(id: string, body: CallDetailRequest, options?: ReadOptions) {
    const value = await this.transport.request("POST", path(id, "/call-details"), request(options, body));
    if (!Array.isArray(value)) throw new TetherError("invalid_response");
    return value.map((item: unknown) => decode("CallDetail", item));
  }
  create(body: CreateSessionRequest, options?: ReadOptions) { return this.write("SessionSummary", "POST", "sessions", body, options); }
  update(id: string, body: UpdateSessionRequest, options?: ReadOptions) { return this.write("SessionSummary", "PATCH", path(id), body, options); }
  async send(id: string, body: SendRequest, options?: ReadOptions) {
    return this.handle(id, await this.write("AcceptedTurn", "POST", path(id, "/turns"), body, options));
  }
  async confirm(id: string, invocation: string, body: ConfirmRequest, options?: ReadOptions) {
    const value = await this.transport.request("POST", path(id, `/confirmations/${encodeURIComponent(invocation)}`), request(options, body));
    if (value && typeof value === "object" && "turn_id" in value) return this.handle(id, decode("AcceptedTurn", value));
    decode("ConfirmResponse", value);
    return null;
  }
  async resume(id: string, options?: ReadOptions) { return this.handle(id, await this.write("AcceptedTurn", "POST", path(id, "/resume"), undefined, options)); }
  resolve(id: string, invocation: string, body: ResolveRequest, options?: ReadOptions) {
    return this.write("SessionSummary", "POST", path(id, `/effects/${encodeURIComponent(invocation)}/resolve`), body, options);
  }
  close(id: string, options?: ReadOptions) { return this.write("SessionSummary", "POST", path(id, "/close"), undefined, options); }
  restore(id: string, options?: ReadOptions) { return this.write("SessionSummary", "POST", path(id, "/restore"), undefined, options); }
  linkHistory(id: string, body: HistoryLinkRequest, options?: ReadOptions) { return this.write("Entry", "POST", path(id, "/history-links"), body, options); }
  async cancel(id: string, options?: ReadOptions) { return this.cancelRequest("POST", path(id, "/cancel"), options); }
  async cancelTurn(id: string, turnId: string, options?: ReadOptions) { return this.cancelRequest("DELETE", path(id, `/turns/${encodeURIComponent(turnId)}`), options); }
  private async cancelRequest(method: string, resource: string, options?: ReadOptions) {
    const value = await this.transport.request(method, resource, request(options));
    if (typeof value !== "boolean") throw new TetherError("invalid_response");
    return value;
  }
  async turnResult(id: string, turnId: string, options?: ReadOptions) {
    const value = await this.transport.request("GET", path(id, `/turns/${encodeURIComponent(turnId)}`), request(options));
    return value === null ? null : decode("TurnResult", value);
  }
  follow(options?: FollowOptions) { return follow(this.transport, options); }
  private handle(id: string, accepted: AcceptedTurn): TurnHandle {
    return {
      turnId: accepted.turn_id,
      result: (options = {}) => this.waitForResult(id, accepted.turn_id, options),
      cancel: (options) => this.cancelTurn(id, accepted.turn_id, options),
    };
  }
  private async waitForResult(id: string, turnId: string, options: { signal?: AbortSignal | undefined; timeoutMs?: number }): Promise<TurnResult> {
    const signal = AbortSignal.any([AbortSignal.timeout(options.timeoutMs ?? 120000), ...options.signal ? [options.signal] : []]);
    while (!signal.aborted) {
      const value = await this.turnResult(id, turnId, { signal });
      if (value !== null) return value;
      await wait(250, signal);
    }
    throw new TetherError("result_wait_expired");
  }
}
