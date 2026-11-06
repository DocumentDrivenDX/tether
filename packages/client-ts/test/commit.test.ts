import { expect, it } from "vitest";
import { TetherClient } from "../src/client.js";

function event(sequence: string, final: boolean, session: string): string {
  const id = final ? `id: ${sequence}\n` : "";
  const value = { type: "session_removed", seq: sequence, session_id: session, last_in_commit: final };
  return `event: session_removed\n${id}data: ${JSON.stringify(value)}\n\n`;
}

it("@covers US-066-AC4 replays incomplete multi-event commits and resumes only after their final event", async () => {
  const requests: URL[] = [];
  const sequence = "9007199254740993";
  const fetch: typeof globalThis.fetch = async (input) => {
    requests.push(new URL(String(input)));
    const body = requests.length === 1
      ? event(sequence, false, "first")
      : requests.length === 2
        ? event(sequence, false, "first") + event(sequence, true, "last")
        : event("9007199254740994", true, "next");
    return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
  };
  const controller = new AbortController();
  const client = new TetherClient({ baseUrl: "https://fabricated.test/api/v1", fetch });
  const iterator = client.follow({ since: 4n, signal: controller.signal })[Symbol.asyncIterator]();
  try {
    const first = await iterator.next();
    expect(first.value).toMatchObject({ seq: 9007199254740993n, session_id: "first", last_in_commit: false });
    expect(requests.map((url) => url.searchParams.get("since"))).toEqual(["4", "4"]);
    const final = await iterator.next();
    expect(final.value).toMatchObject({ seq: 9007199254740993n, session_id: "last", last_in_commit: true });
    expect(requests).toHaveLength(2);
    const next = await iterator.next();
    expect(next.value).toMatchObject({ seq: 9007199254740994n, last_in_commit: true });
    expect(requests.map((url) => url.searchParams.get("since"))).toEqual(["4", "4", sequence]);
  } finally {
    controller.abort();
    await iterator.return?.();
  }
});
