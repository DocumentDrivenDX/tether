import { describe, expect, it } from "vitest";
import { readSse } from "../src/sse.js";
import { hydrate, serialize } from "../src/exact.js";

function stream(parts: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({ start(controller) {
    for (const part of parts) controller.enqueue(new TextEncoder().encode(part));
    controller.close();
  } });
}

describe("wire boundaries", () => {
  it("@covers US-067-AC5 retains exact counter and opaque user JSON", () => {
    const value = hydrate({ seq: "9007199254740993", facts: { index: 42 }, settings: { seq: 1 } });
    expect(value.seq).toBe(9007199254740993n);
    expect(value.facts.index).toBe(42);
    expect(JSON.parse(serialize(value))).toEqual({ seq: "9007199254740993", facts: { index: 42 }, settings: { seq: 1 } });
  });
  it("rejects imprecise numeric counters instead of repairing them", () => {
    expect(() => hydrate({ seq: 9007199254740992 })).toThrow("invalid_counter");
  });
  it("parses fragmented CRLF and only explicit commit IDs", async () => {
    const events = [];
    for await (const event of readSse(stream([": beat\r", "\nevent: reply_text\r\ndata: first\r\n\r", "\nid: 9007199254740993\ndata: second\n\n"]))) events.push(event);
    expect(events).toEqual([{ event: "reply_text", data: "first" }, { event: "message", data: "second", id: "9007199254740993" }]);
  });
  it("discards partial frames on disconnect", async () => {
    const events = [];
    for await (const event of readSse(stream(["id: 8\ndata: incomplete"]))) events.push(event);
    expect(events).toEqual([]);
  });
  it("bounds event memory", async () => {
    const consume = async () => { for await (const event of readSse(stream(["data: too long\n\n"]), { maxFrameBytes: 4 })) void event; };
    await expect(consume()).rejects.toThrow("event_too_large");
  });
});
