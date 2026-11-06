import { readFile } from "node:fs/promises";
import { describe, expect, it, vi } from "vitest";
import { TetherClient } from "../src/client.js";
import { TetherError } from "../src/errors.js";
import { decode } from "../src/wire/validators.js";

const summary = {
  id: "session 1",
  owner: "alice",
  ref: "ref/one",
  created_at: "2026-10-29T12:00:00Z",
  updated_at: "2026-10-29T12:00:00Z",
  entry_count: "9007199254740993",
  last_seq: "9007199254740993",
};
const view = { summary, settings: {}, specs: {}, pending: null, tools: [], tools_status: "not_loaded" };
const turn = { turn_id: "turn 1", stop_reason: "completed", messages: [], pending: [] };

const accepted = { request_id: "request-1", turn_id: "turn 1", state: "accepted", result_url: "/api/v1/result" };
const responseRoutes: Array<[string, RegExp, unknown]> = [
  ["GET", /\/capabilities$/, { methods: {}, tiers: { live: "available", archive: "available" } }],
  ["GET", /\/sessions$/, { items: [], next: null }],
  ["POST", /\/sessions$/, summary],
  ["GET", /\/by-ref\/[^/]+$/, summary],
  ["GET", /\/snapshot$/, { view, through_index: "-1", seq: "9007199254740993" }],
  ["GET", /\/entries$/, { items: [], next_start: null, partial: false }],
  ["GET", /\/history$/, { items: [], next_start: null, partial: false }],
  ["GET", /\/pending$/, null],
  ["GET", /\/effects$/, { items: [], next_start: null }],
  ["POST", /\/call-details$/, []],
  ["POST", /\/(?:turns|resume)$/, accepted],
  ["DELETE", /\/turns\/[^/]+$/, false],
  ["GET", /\/turns\/[^/]+$/, turn],
  ["POST", /\/cancel$/, true],
  ["POST", /\/confirmations\/accept$/, accepted],
  ["POST", /\/confirmations\/[^/]+$/, { state: view, accepted: false }],
  ["POST", /\/history-links$/, { kind: "message", index: "0", at: "2026-10-29T12:00:00Z", facts: {} }],
  ["POST", /\/effects\/[^/]+\/resolve$/, summary],
  ["PATCH", /\/sessions\/[^/]+$/, summary],
  ["POST", /\/(?:close|restore)$/, summary],
  ["GET", /\/sessions\/[^/]+$/, view],
];

function responseFor(url: URL, method: string): unknown {
  const route = responseRoutes.find(([candidate, pattern]) => candidate === method && pattern.test(url.pathname));
  return route ? route[2] : { code: "unexpected_test_route" };
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}

function streamResponse(value: string): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(value));
      controller.close();
    },
  });
  return new Response(body, { headers: { "content-type": "text/event-stream" } });
}

describe("typed HTTP client", () => {
  it("maps the public methods onto versioned routes and preserves exact counters and opaque JSON", async () => {
    const calls: Array<{ url: URL; method: string; body: unknown; authorization: string | null }> = [];
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input.toString());
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) as unknown : undefined;
      calls.push({ url, method, body, authorization: new Headers(init?.headers).get("authorization") });
      return jsonResponse(responseFor(url, method));
    });
    let credential = 0;
    const client = new TetherClient({
      baseUrl: "https://example.test/prefix/api/v1",
      fetch,
      headers: async () => ({ authorization: ["Bearer", `token-${++credential}`].join(" ") }),
    });

    const caps = await client.capabilities();
    await client.list({ status: "running", cursor: "next", limit: 2 });
    const created = await client.create({ request_id: "create-1", settings: { seq: 7, count: 8 } });
    expect(created.entry_count).toBe(9007199254740993n);
    expect(await client.find("ref/one")).toMatchObject({ id: "session 1" });
    expect(await client.get("session 1")).toMatchObject({ summary: { id: "session 1" } });
    expect((await client.snapshot("session 1")).through_index).toBe(-1n);
    await client.entries("session 1", { start: 9007199254740993n });
    await client.history("session 1", { tier: "archive", start: 1n });
    expect(await client.pending("session 1")).toBeNull();
    await client.effects("session 1");
    await client.callDetail("session 1", { model_call_ids: ["call-1"] });
    await client.update("session 1", { title: "updated" });
    const accepted = await client.send("session 1", { request_id: "send-1", text: "hello" });
    expect(accepted.turnId).toBe("turn 1");
    expect(await client.turnResult("session 1", accepted.turnId)).toMatchObject({ turn_id: "turn 1" });
    expect(await client.cancelTurn("session 1", accepted.turnId)).toBe(false);
    expect(await accepted.cancel()).toBe(false);
    expect(await accepted.result({ timeoutMs: 1000 })).toMatchObject({ turn_id: "turn 1" });
    expect(await client.confirm("session 1", "invoke-1", { answer: { approved: true } })).toBeNull();
    expect((await client.confirm("session 1", "accept", { answer: { approved: true } }))?.turnId).toBe("turn 1");
    const resumed = await client.resume("session 1");
    expect(resumed.turnId).toBe("turn 1");
    await client.resolve("session 1", "invoke-1", { outcome: "completed", reason: "verified" });
    expect(await client.cancel("session 1")).toBe(true);
    await client.close("session 1");
    await client.restore("session 1");
    await client.linkHistory("session 1", {
      message_index: 1n,
      previous: { source: "old", key: "k1", content_hash: "a" },
      verified: { source: "new", key: "k2", content_hash: "b" },
      key: "link-1",
    });

    expect(caps.tiers.live).toBe("available");
    expect(calls.map(({ method, url }) => `${method} ${url.pathname.replace("/prefix/api/v1/", "")}`)).toEqual([
      "GET capabilities", "GET sessions", "POST sessions", "GET sessions/by-ref/ref%2Fone",
      "GET sessions/session%201", "GET sessions/session%201/snapshot", "GET sessions/session%201/entries",
      "GET sessions/session%201/history", "GET sessions/session%201/pending", "GET sessions/session%201/effects",
      "POST sessions/session%201/call-details", "PATCH sessions/session%201", "POST sessions/session%201/turns",
      "GET sessions/session%201/turns/turn%201", "DELETE sessions/session%201/turns/turn%201",
      "DELETE sessions/session%201/turns/turn%201", "GET sessions/session%201/turns/turn%201",
      "POST sessions/session%201/confirmations/invoke-1",
      "POST sessions/session%201/confirmations/accept",
      "POST sessions/session%201/resume",
      "POST sessions/session%201/effects/invoke-1/resolve", "POST sessions/session%201/cancel",
      "POST sessions/session%201/close", "POST sessions/session%201/restore", "POST sessions/session%201/history-links",
    ]);
    expect(calls[2]?.body).toMatchObject({ request_id: "create-1", settings: { seq: 7, count: 8 } });
    expect(calls.every((call, index) => call.authorization === ["Bearer", `token-${index + 1}`].join(" "))).toBe(true);
    expect(credential).toBe(calls.length);
  });

  it("rejects missing Change discriminators and malformed counter strings", () => {
    expect(() => decode("Change", { session_removed: true })).toThrow("invalid_response");
    expect(decode("SessionSnapshot", { view, through_index: "-1", seq: "0" }).through_index).toBe(-1n);
    expect(() => decode("SessionSnapshot", { view, through_index: "-2", seq: "1" })).toThrow("invalid_response");
    expect(() => decode("SessionSnapshot", { view, through_index: "0", seq: "01" })).toThrow("invalid_response");
  });

  it("runs generated string-length checks with the browser-compatible standalone helper", () => {
    expect(decode("AcceptedTurn", accepted).turn_id).toBe("turn 1");
    expect(() => decode("AcceptedTurn", { ...accepted, turn_id: "" })).toThrow("invalid_response");
  });

  it("does not use dynamic compilation when decoding a schema-validated response", () => {
    const original = globalThis.Function;
    globalThis.Function = (() => { throw new Error("dynamic compilation blocked"); }) as FunctionConstructor;
    try {
      expect(decode("SessionSummary", summary).last_seq).toBe(9007199254740993n);
    } finally {
      globalThis.Function = original;
    }
  });

  it("ships validators as static code that does not require eval, Function, or CommonJS loading", async () => {
    const source = await readFile("packages/client-ts/src/wire/validators.generated.js", "utf8");
    expect(source).not.toMatch(/\bnew\s+Function\s*\(/);
    expect(source).not.toMatch(/\beval\s*\(/);
    expect(source).not.toMatch(/\brequire\s*\(/);
    expect(source).toContain("export const validators =");
  });

  it("reports invalid JSON responses without exposing their payload", async () => {
    const client = new TetherClient({ baseUrl: "https://example.test", fetch: async () => new Response("not json") });
    await expect(client.capabilities()).rejects.toBeInstanceOf(TetherError);
  });

  it("@covers US-067-AC5 reconnects with the exact committed counter after an incomplete commit", async () => {
    const requests: URL[] = [];
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input.toString());
      requests.push(url);
      if (requests.length === 1) {
        return streamResponse('event: session_removed\ndata: {"type":"session_removed","seq":"15","session_id":"s-1","last_in_commit":false}\n\n');
      }
      const seq = requests.length === 2 ? "9007199254740993" : "9007199254740994";
      return streamResponse(`event: session_removed\nid: ${seq}\ndata: {"type":"session_removed","seq":"${seq}","session_id":"s-1","last_in_commit":true}\n\n`);
    });
    const controller = new AbortController();
    const client = new TetherClient({ baseUrl: "https://example.test/api/v1", fetch });
    const feed = client.follow({ since: 4n, signal: controller.signal });
    const iterator = feed[Symbol.asyncIterator]();
    const received = await iterator.next();
    const next = await iterator.next();
    controller.abort();
    await iterator.return?.();

    expect(received.value).toMatchObject({ type: "session_removed", seq: 9007199254740993n });
    expect(next.value).toMatchObject({ type: "session_removed", seq: 9007199254740994n });
    expect(requests).toHaveLength(3);
    expect(requests.map((url) => url.searchParams.get("since"))).toEqual(["4", "4", "9007199254740993"]);
  });
});
