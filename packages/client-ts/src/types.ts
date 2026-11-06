import type * as Wire from "./wire/generated.js";
import type { Exact } from "./exact.js";
export type SessionSummary = Exact<Wire.SessionSummary>;
export type SessionView = Exact<Wire.SessionView>;
export type SessionSnapshot = Exact<Wire.SessionSnapshot>;
export type SessionPage = Exact<Wire.PageSessionSummary>;
export type HistoryPage = Exact<Wire.HistoryPage>;
export type EntryPage = Exact<Wire.EntryPage>;
export type EffectPage = Exact<Wire.EffectPage>;
export type CallDetail = Exact<Wire.CallDetail>;
export type PendingConfirmation = Exact<Wire.PendingConfirmation>;
export type TurnResult = Exact<Wire.TurnResult>;
export type Change = Exact<Wire.Change>;
export type Entry = Exact<Wire.Entry>;
export type Message = Exact<Wire.Message>;
export type Capabilities = Wire.ClientCapabilities;
export type CreateSessionRequest = Exact<Wire.CreateSessionRequest>;
export type UpdateSessionRequest = Exact<Wire.UpdateSessionRequest>;
export type SendRequest = Exact<Wire.SendRequest>;
export type ConfirmRequest = Exact<Wire.ConfirmRequest>;
export type ResolveRequest = Exact<Wire.ResolveRequest>;
export type HistoryLinkRequest = Exact<Wire.HistoryLinkRequest>;
export type CallDetailRequest = Exact<Wire.CallDetailRequest>;
export type AcceptedTurn = Wire.AcceptedTurn;
export interface ReadOptions { signal?: AbortSignal | undefined; tier?: "live" | "archive" }
export interface PageOptions extends ReadOptions { start?: bigint; limit?: number; reverse?: boolean }
export interface ListOptions extends ReadOptions { cursor?: string; limit?: number; status?: string; text?: string }
export interface FollowOptions { sessionId?: string; since?: bigint; signal?: AbortSignal | undefined; onReconnect?: () => void }
export interface TurnHandle {
  readonly turnId: string;
  result(options?: { signal?: AbortSignal | undefined; timeoutMs?: number }): Promise<TurnResult>;
  cancel(options?: ReadOptions): Promise<boolean>;
}
export interface SessionClient {
  capabilities(options?: ReadOptions): Promise<Capabilities>;
  list(options?: ListOptions): Promise<SessionPage>;
  find(ref: string, options?: ReadOptions): Promise<SessionSummary | null>;
  get(id: string, options?: ReadOptions): Promise<SessionView>;
  snapshot(id: string, options?: ReadOptions): Promise<SessionSnapshot>;
  entries(id: string, options?: PageOptions): Promise<EntryPage>;
  history(id: string, options?: PageOptions): Promise<HistoryPage>;
  pending(id: string, options?: ReadOptions): Promise<PendingConfirmation | null>;
  effects(id: string, options?: PageOptions): Promise<EffectPage>;
  callDetail(id: string, body: CallDetailRequest, options?: ReadOptions): Promise<CallDetail[]>;
  create(body: CreateSessionRequest, options?: ReadOptions): Promise<SessionSummary>;
  update(id: string, body: UpdateSessionRequest, options?: ReadOptions): Promise<SessionSummary>;
  send(id: string, body: SendRequest, options?: ReadOptions): Promise<TurnHandle>;
  confirm(id: string, invocation: string, body: ConfirmRequest, options?: ReadOptions): Promise<TurnHandle | null>;
  resume(id: string, options?: ReadOptions): Promise<TurnHandle>;
  resolve(id: string, invocation: string, body: ResolveRequest, options?: ReadOptions): Promise<SessionSummary>;
  cancel(id: string, options?: ReadOptions): Promise<boolean>;
  cancelTurn(id: string, turnId: string, options?: ReadOptions): Promise<boolean>;
  close(id: string, options?: ReadOptions): Promise<SessionSummary>;
  restore(id: string, options?: ReadOptions): Promise<SessionSummary>;
  linkHistory(id: string, body: HistoryLinkRequest, options?: ReadOptions): Promise<Entry>;
  turnResult(id: string, turnId: string, options?: ReadOptions): Promise<TurnResult | null>;
  follow(options?: FollowOptions): AsyncIterable<Change>;
}
