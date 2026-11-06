/* Generated from the Python HTTP schema. Do not edit. */

export type RequestId = string | null;
export type ResultUrl = string;
export type State = "accepted";
export type TurnId = string;
export type Approved = boolean;
export type Note = string | null;
export type Filename = string;
export type Id = string;
export type MediaType = string;
export type Read = boolean;
export type SizeBytes = string;
export type Withheld = boolean;
export type AttemptId = string;
export type ModelCallId = string;
export type Purpose = "answer" | "summary" | "correction";
export type SessionId = string;
export type SpanId = string | null;
export type TraceId = string | null;
export type TurnId1 = string;
export type Reason = string | null;
export type Kind = "message" | "model_exchange" | "summary";
export type Attachments = AttachmentRef[];
export type Content = string;
export type IsError = boolean;
export type Role = "user" | "assistant" | "tool";
export type ToolCallId = string | null;
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue = unknown;
export type Id1 = string;
export type Name = string;
export type ToolCalls = ToolCall[];
export type SchemaVersion = 1;
export type Summary = string | null;
export type Codec = string;
export type Component = "record" | "request" | "response" | "assistant" | "summary";
export type Key = string;
export type Kind1 = "message" | "model_exchange" | "summary";
export type Owner = string;
export type Provider = string;
export type SessionId1 = string;
export type Sha256 = string;
export type RetryAfterSeconds = number | null;
export type Status =
  | "ready"
  | "pending"
  | "denied"
  | "unavailable"
  | "missing"
  | "partial"
  | "ambiguous"
  | "unsupported"
  | "integrity_error";
export type Observations = HistoryValue[];
export type Reason1 = string | null;
export type Endpoint = string;
export type Provider1 = string;
export type ProviderInvocationId = string | null;
export type ProviderRequestId = string | null;
export type RequestIdSource = "header" | "body" | "none";
export type ResponseId = string | null;
export type SchemaProfile = string;
export type Receipts = ResponseReceipt[];
export type Status1 =
  | "ready"
  | "pending"
  | "denied"
  | "unavailable"
  | "missing"
  | "partial"
  | "ambiguous"
  | "unsupported"
  | "integrity_error";
export type ModelCallIds = string[];
export type ArgsDigest = string;
export type CallId = string;
export type Effect = "read_only" | "effectful";
export type Position = string;
export type Replay = "safe" | "unsafe";
export type Tool = string;
export type ToolDigest = string;
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "Change".
 */
export type Change =
  | SessionUpdatedEvent
  | SessionRemovedEvent
  | EntriesAppendedEvent
  | ReplyTextEvent
  | ReplyResetEvent
  | LaggedEvent
  | ResyncRequiredEvent;
export type LastInCommit = boolean;
export type Seq = string;
export type ActiveTurnId = string | null;
export type CreatedAt = string;
export type EntryCount = string;
export type Id2 = string;
export type LastSeq = string;
export type LastTurnAt = string | null;
export type MessageCount = string;
export type Owner1 = string;
export type Partial = boolean;
export type Pending = boolean;
export type Preview = string | null;
export type Ref = string | null;
export type Resumable = boolean;
export type SpecId = string | null;
export type Status2 = "idle" | "running" | "waiting" | "interrupted" | "closed";
export type Tags = string[];
export type Tier = "live" | "archive";
export type Title = string | null;
export type TurnCount = string;
export type UnresolvedEffectCount = string;
export type UpdatedAt = string;
export type CacheReadTokens = string | null;
export type CacheWriteTokens = string | null;
export type InputTokens = string;
export type OutputTokens = string;
export type ReasoningTokens = string | null;
export type UsageComplete = boolean;
export type Type = "session_updated";
export type LastInCommit1 = boolean;
export type Seq1 = string;
export type SessionId2 = string;
export type Type1 = "session_removed";
export type At = string;
export type CallManifest1 = CallManifest[];
export type Index = string;
export type Key1 = string | null;
export type Kind2 = string;
export type TurnId2 = string | null;
export type Entries = Entry[];
export type LastInCommit2 = boolean;
export type Seq2 = string;
export type SessionId3 = string;
export type Type2 = "entries_appended";
export type AttemptId1 = string;
export type Offset = string;
export type SessionId4 = string;
export type Text = string;
export type TurnId3 = string;
export type Type3 = "reply_text";
export type AttemptId2 = string;
export type Reason2 = "outage" | "cancelled" | "reconnect";
export type SessionId5 = string;
export type TurnId4 = string;
export type Type4 = "reply_reset";
export type Missed = string;
export type Type5 = "lagged";
export type Reason3 = "too_old" | "gap";
export type Type6 = "resync_required";
export type Accepted = false;
export type ActorId = string;
export type ArgsDigest1 = string;
export type Arguments1 = {
  [k: string]: JsonValue;
} | null;
export type Description = string;
export type ExpiresAt = string | null;
export type HistoryStatus =
  | "not_loaded"
  | (
      | "ready"
      | "pending"
      | "denied"
      | "unavailable"
      | "missing"
      | "partial"
      | "ambiguous"
      | "unsupported"
      | "integrity_error"
    );
export type InvocationId = string;
export type Reason4 = string | null;
export type RequestedAt = string;
export type SessionId6 = string;
export type SpecDigest = string;
export type Tool1 = string;
export type ToolDigest1 = string;
export type Digest = string | null;
export type Id3 = string;
export type Version = string | null;
export type Description1 = string | null;
export type Name1 = string;
export type ToolDigest2 = string | null;
export type Version1 = string | null;
export type Tools = ToolInfo[];
export type ToolsStatus = "not_loaded" | "ready" | "unsupported";
export type RequestId1 = string;
export type Spec = string | null;
export type Tags1 = string[];
export type Title1 = string | null;
export type IntentIndex = string;
export type InvocationId1 = string;
export type Reason5 = string;
export type Tool2 = string;
export type Items = EffectInfo[];
export type NextStart = string | null;
export type Items1 = Entry[];
export type NextStart1 = string | null;
export type Partial1 = boolean;
export type Key2 = string;
export type MessageIndex = string;
export type EntryIndex = string;
export type Items2 = HistorySlot[];
export type NextStart2 = string | null;
export type Partial2 = boolean;
export type Items3 = SessionSummary[];
export type Next = string | null;
export type Code = string;
export type Detail = string;
export type Instance = string | null;
export type Locator = {
  [k: string]: string;
} | null;
export type RetryAfterSeconds1 = number | null;
export type Retryable = boolean;
export type Status3 = number;
export type Title2 = string;
export type Type7 = string;
export type Outcome = "completed" | "not_completed";
export type Reason6 = string;
export type Attachments1 = AttachmentRef[];
export type RequestId2 = string;
export type Spec1 = string | null;
export type Text1 = string;
export type Order = "updated_desc" | "created_desc" | "created_asc";
export type Owner2 = string;
export type Ref1 = string | null;
export type Status4 = string[] | null;
export type TagsAll = string[];
export type Text2 = string | null;
export type UpdatedAfter = string | null;
export type UpdatedBefore = string | null;
export type Seq3 = string;
export type ThroughIndex = string;
export type Messages = Message[];
export type Pending1 = PendingConfirmation[];
export type Reason7 = string | null;
export type StopReason = string;
export type TurnId5 = string;
export type Settings2 = {
  [k: string]: JsonValue;
} | null;
export type Tags2 = string[] | null;
export type Title3 = string | null;

export interface TetherWire {
  AcceptedTurn: AcceptedTurn;
  Answer: Answer;
  AttachmentRef: AttachmentRef;
  AttemptIdentity: AttemptIdentity;
  CallDetail: CallDetail;
  CallDetailRequest: CallDetailRequest;
  CallManifest: CallManifest;
  Change: Change;
  ClientCapabilities: ClientCapabilities;
  ConfirmRequest: ConfirmRequest;
  ConfirmResponse: ConfirmResponse;
  CreateSessionRequest: CreateSessionRequest;
  EffectInfo: EffectInfo;
  EffectPage: EffectPage;
  EntriesAppendedEvent: EntriesAppendedEvent;
  Entry: Entry;
  EntryPage: EntryPage;
  HistoryLinkRequest: HistoryLinkRequest;
  HistoryPage: HistoryPage;
  HistoryRecord: HistoryRecord;
  HistoryRef: HistoryRef;
  HistorySlot: HistorySlot;
  HistoryValue: HistoryValue;
  JsonValue: JsonValue;
  LaggedEvent: LaggedEvent;
  Message: Message;
  PageSessionSummary: PageSessionSummary;
  PendingConfirmation: PendingConfirmation;
  ProblemDetails: ProblemDetails;
  ReplyResetEvent: ReplyResetEvent;
  ReplyTextEvent: ReplyTextEvent;
  ResolveRequest: ResolveRequest;
  ResponseReceipt: ResponseReceipt;
  ResyncRequiredEvent: ResyncRequiredEvent;
  SendRequest: SendRequest;
  SessionQuery: SessionQuery;
  SessionRemovedEvent: SessionRemovedEvent;
  SessionSnapshot: SessionSnapshot;
  SessionSummary: SessionSummary;
  SessionUpdatedEvent: SessionUpdatedEvent;
  SessionView: SessionView;
  SpecRef: SpecRef;
  TokenUsage: TokenUsage;
  ToolCall: ToolCall;
  ToolInfo: ToolInfo;
  TurnResult: TurnResult;
  UpdateSessionRequest: UpdateSessionRequest;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "AcceptedTurn".
 */
export interface AcceptedTurn {
  request_id?: RequestId;
  result_url: ResultUrl;
  state?: State;
  turn_id: TurnId;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "Answer".
 */
export interface Answer {
  approved: Approved;
  note?: Note;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "AttachmentRef".
 */
export interface AttachmentRef {
  filename: Filename;
  id: Id;
  media_type: MediaType;
  read: Read;
  size_bytes: SizeBytes;
  withheld?: Withheld;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "AttemptIdentity".
 */
export interface AttemptIdentity {
  attempt_id: AttemptId;
  model_call_id: ModelCallId;
  purpose: Purpose;
  session_id: SessionId;
  span_id?: SpanId;
  trace_id?: TraceId;
  turn_id: TurnId1;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "CallDetail".
 */
export interface CallDetail {
  identity: AttemptIdentity;
  observations?: Observations;
  reason?: Reason1;
  receipt?: ResponseReceipt | null;
  receipts?: Receipts;
  status: Status1;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "HistoryValue".
 */
export interface HistoryValue {
  reason?: Reason;
  record?: HistoryRecord | null;
  ref: HistoryRef;
  retry_after_seconds?: RetryAfterSeconds;
  source_locator?: SourceLocator;
  status: Status;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "HistoryRecord".
 */
export interface HistoryRecord {
  kind: Kind;
  message?: Message | null;
  request?: unknown;
  response?: unknown;
  schema_version?: SchemaVersion;
  summary?: Summary;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "Message".
 */
export interface Message {
  attachments?: Attachments;
  content: Content;
  is_error?: IsError;
  role: Role;
  tool_call_id?: ToolCallId;
  tool_calls?: ToolCalls;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ToolCall".
 */
export interface ToolCall {
  arguments: Arguments;
  id: Id1;
  name: Name;
}
export interface Arguments {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "HistoryRef".
 */
export interface HistoryRef {
  codec?: Codec;
  component?: Component;
  key: Key;
  kind?: Kind1;
  owner?: Owner;
  provider: Provider;
  session_id?: SessionId1;
  sha256: Sha256;
}
export interface SourceLocator {
  [k: string]: string;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ResponseReceipt".
 */
export interface ResponseReceipt {
  endpoint: Endpoint;
  provider: Provider1;
  provider_invocation_id?: ProviderInvocationId;
  provider_request_id?: ProviderRequestId;
  request_id_source: RequestIdSource;
  response_id?: ResponseId;
  schema_profile: SchemaProfile;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "CallDetailRequest".
 */
export interface CallDetailRequest {
  model_call_ids: ModelCallIds;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "CallManifest".
 */
export interface CallManifest {
  args_digest: ArgsDigest;
  call_id: CallId;
  effect: Effect;
  position: Position;
  replay: Replay;
  tool: Tool;
  tool_digest: ToolDigest;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SessionUpdatedEvent".
 */
export interface SessionUpdatedEvent {
  last_in_commit: LastInCommit;
  seq: Seq;
  summary: SessionSummary;
  type: Type;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SessionSummary".
 */
export interface SessionSummary {
  active_turn_id?: ActiveTurnId;
  created_at: CreatedAt;
  entry_count?: EntryCount;
  id: Id2;
  last_seq?: LastSeq;
  last_turn_at?: LastTurnAt;
  message_count?: MessageCount;
  owner: Owner1;
  partial?: Partial;
  pending?: Pending;
  preview?: Preview;
  ref?: Ref;
  resumable?: Resumable;
  spec_id?: SpecId;
  status?: Status2;
  tags?: Tags;
  tier?: Tier;
  title?: Title;
  turn_count?: TurnCount;
  unresolved_effect_count?: UnresolvedEffectCount;
  updated_at: UpdatedAt;
  usage?: TokenUsage;
  usage_complete?: UsageComplete;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "TokenUsage".
 */
export interface TokenUsage {
  cache_read_tokens?: CacheReadTokens;
  cache_write_tokens?: CacheWriteTokens;
  input_tokens: InputTokens;
  output_tokens: OutputTokens;
  reasoning_tokens?: ReasoningTokens;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SessionRemovedEvent".
 */
export interface SessionRemovedEvent {
  last_in_commit: LastInCommit1;
  seq: Seq1;
  session_id: SessionId2;
  type: Type1;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "EntriesAppendedEvent".
 */
export interface EntriesAppendedEvent {
  entries: Entries;
  last_in_commit: LastInCommit2;
  seq: Seq2;
  session_id: SessionId3;
  type: Type2;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "Entry".
 */
export interface Entry {
  at: At;
  call_manifest?: CallManifest1;
  facts?: Facts;
  history_ref?: HistoryRef | null;
  index: Index;
  key?: Key1;
  kind: Kind2;
  turn_id?: TurnId2;
}
export interface Facts {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ReplyTextEvent".
 */
export interface ReplyTextEvent {
  attempt_id: AttemptId1;
  offset: Offset;
  session_id: SessionId4;
  text: Text;
  turn_id: TurnId3;
  type: Type3;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ReplyResetEvent".
 */
export interface ReplyResetEvent {
  attempt_id: AttemptId2;
  reason: Reason2;
  session_id: SessionId5;
  turn_id: TurnId4;
  type: Type4;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "LaggedEvent".
 */
export interface LaggedEvent {
  missed: Missed;
  type: Type5;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ResyncRequiredEvent".
 */
export interface ResyncRequiredEvent {
  reason: Reason3;
  type: Type6;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ClientCapabilities".
 */
export interface ClientCapabilities {
  methods: Methods;
  tiers: Tiers;
}
export interface Methods {
  [k: string]: "supported" | "unsupported";
}
export interface Tiers {
  [k: string]: "available" | "unavailable";
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ConfirmRequest".
 */
export interface ConfirmRequest {
  answer: Answer;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ConfirmResponse".
 */
export interface ConfirmResponse {
  accepted?: Accepted;
  state: SessionView;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SessionView".
 */
export interface SessionView {
  pending?: PendingConfirmation | null;
  settings?: Settings;
  specs?: Specs;
  summary: SessionSummary;
  tools?: Tools;
  tools_status?: ToolsStatus;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "PendingConfirmation".
 */
export interface PendingConfirmation {
  actor_id: ActorId;
  args_digest: ArgsDigest1;
  arguments: Arguments1;
  description?: Description;
  expires_at?: ExpiresAt;
  history_ref?: HistoryRef | null;
  history_status?: HistoryStatus;
  invocation_id: InvocationId;
  reason?: Reason4;
  requested_at: RequestedAt;
  session_id: SessionId6;
  spec_digest: SpecDigest;
  tool: Tool1;
  tool_digest: ToolDigest1;
}
export interface Settings {
  [k: string]: JsonValue;
}
export interface Specs {
  [k: string]: SpecRef;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SpecRef".
 */
export interface SpecRef {
  digest?: Digest;
  id: Id3;
  version?: Version;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ToolInfo".
 */
export interface ToolInfo {
  description?: Description1;
  name: Name1;
  tool_digest?: ToolDigest2;
  version?: Version1;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "CreateSessionRequest".
 */
export interface CreateSessionRequest {
  request_id: RequestId1;
  settings?: Settings1;
  spec?: Spec;
  tags?: Tags1;
  title?: Title1;
}
export interface Settings1 {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "EffectInfo".
 */
export interface EffectInfo {
  intent_index: IntentIndex;
  invocation_id: InvocationId1;
  reason: Reason5;
  tool: Tool2;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "EffectPage".
 */
export interface EffectPage {
  items: Items;
  next_start?: NextStart;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "EntryPage".
 */
export interface EntryPage {
  items: Items1;
  next_start: NextStart1;
  partial?: Partial1;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "HistoryLinkRequest".
 */
export interface HistoryLinkRequest {
  key: Key2;
  message_index: MessageIndex;
  previous: HistoryRef;
  verified: HistoryRef;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "HistoryPage".
 */
export interface HistoryPage {
  items: Items2;
  next_start: NextStart2;
  partial?: Partial2;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "HistorySlot".
 */
export interface HistorySlot {
  entry_index: EntryIndex;
  value: HistoryValue;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "PageSessionSummary".
 */
export interface PageSessionSummary {
  items: Items3;
  next?: Next;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ProblemDetails".
 */
export interface ProblemDetails {
  code: Code;
  detail: Detail;
  instance?: Instance;
  locator?: Locator;
  retry_after_seconds?: RetryAfterSeconds1;
  retryable: Retryable;
  status: Status3;
  title: Title2;
  type?: Type7;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "ResolveRequest".
 */
export interface ResolveRequest {
  outcome: Outcome;
  reason: Reason6;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SendRequest".
 */
export interface SendRequest {
  attachments?: Attachments1;
  request_id: RequestId2;
  spec?: Spec1;
  text: Text1;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SessionQuery".
 */
export interface SessionQuery {
  order?: Order;
  owner: Owner2;
  ref?: Ref1;
  status?: Status4;
  tags_all?: TagsAll;
  text?: Text2;
  updated_after?: UpdatedAfter;
  updated_before?: UpdatedBefore;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "SessionSnapshot".
 */
export interface SessionSnapshot {
  seq: Seq3;
  through_index: ThroughIndex;
  view: SessionView;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "TurnResult".
 */
export interface TurnResult {
  messages?: Messages;
  pending?: Pending1;
  reason?: Reason7;
  stop_reason: StopReason;
  turn_id: TurnId5;
}
/**
 * This interface was referenced by `TetherWire`'s JSON-Schema
 * via the `definition` "UpdateSessionRequest".
 */
export interface UpdateSessionRequest {
  settings?: Settings2;
  tags?: Tags2;
  title?: Title3;
}
