import { serialize } from "./exact.js";
import { TetherError } from "./errors.js";

export interface TransportSettings {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
  credentials?: RequestCredentials;
  headers?: () => Promise<HeadersInit>;
  timeoutMs?: number;
  maxResponseBytes?: number;
  maxRequestBytes?: number;
}
export interface RequestOptions { signal?: AbortSignal | undefined; query?: Record<string, string>; body?: unknown }

async function boundedJson(response: Response, limit: number): Promise<unknown> {
  if (!response.body) throw new TetherError("empty_response");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let size = 0;
  let text = "";
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > limit) throw new TetherError("response_too_large");
      text += decoder.decode(chunk.value, { stream: true });
    }
    text += decoder.decode();
    return JSON.parse(text) as unknown;
  } catch (error) {
    if (error instanceof TetherError) throw error;
    throw new TetherError("invalid_response");
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}

function httpError(value: unknown, status: number): TetherError {
  const code = value && typeof value === "object" && "code" in value ? value.code : "http_error";
  const safe = typeof code === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(code) ? code : "http_error";
  return new TetherError(safe, { status, retryable: status === 429 || status === 503 });
}

function requestUrl(base: URL, path: string, query: Record<string, string> = {}): URL {
  const url = new URL(path.replace(/^\//, ""), base);
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
  return url;
}

function requestSignal(signal: AbortSignal | undefined, timeoutMs: number, stream: boolean): AbortSignal {
  const timed = stream ? undefined : AbortSignal.timeout(timeoutMs);
  return AbortSignal.any([signal, timed].filter((item): item is AbortSignal => item !== undefined));
}

async function requestInit(
  settings: TransportSettings,
  method: string,
  options: RequestOptions,
  stream: boolean,
): Promise<RequestInit> {
  const headers = new Headers(await settings.headers?.());
  headers.set("accept", stream ? "text/event-stream" : "application/json");
  const init: RequestInit = {
    method,
    headers,
    credentials: settings.credentials ?? "same-origin",
    signal: requestSignal(options.signal, settings.timeoutMs ?? 30000, stream),
  };
  if (options.body !== undefined) {
    const body = serialize(options.body);
    if (new TextEncoder().encode(body).length > (settings.maxRequestBytes ?? 1024 * 1024)) {
      throw new TetherError("request_too_large");
    }
    headers.set("content-type", "application/json");
    init.body = body;
  }
  return init;
}

function transportError(method: string, signal: AbortSignal | undefined): TetherError {
  const aborted = signal?.aborted === true;
  const retryable = !aborted && method === "GET";
  return new TetherError(aborted ? "aborted" : retryable ? "transport_unavailable" : "outcome_uncertain", { retryable });
}

export class Transport {
  readonly settings: TransportSettings;
  readonly base: URL;
  private readonly fetcher: typeof globalThis.fetch;
  constructor(settings: TransportSettings) {
    this.settings = settings;
    this.base = new URL(settings.baseUrl.replace(/\/$/, "") + "/", globalThis.location?.origin);
    if (!["http:", "https:"].includes(this.base.protocol) || this.base.username || this.base.password || this.base.search || this.base.hash) {
      throw new TetherError("invalid_base_url");
    }
    this.fetcher = settings.fetch ?? globalThis.fetch.bind(globalThis);
  }
  async open(method: string, path: string, options: RequestOptions = {}, stream = false): Promise<Response> {
    const url = requestUrl(this.base, path, options.query);
    const init = await requestInit(this.settings, method, options, stream);
    try {
      return await this.fetcher(url, init);
    } catch {
      throw transportError(method, options.signal);
    }
  }
  async request(method: string, path: string, options: RequestOptions = {}): Promise<unknown> {
    const response = await this.open(method, path, options);
    const value = await boundedJson(response, this.settings.maxResponseBytes ?? 4 * 1024 * 1024);
    if (!response.ok) throw httpError(value, response.status);
    return value;
  }
}
