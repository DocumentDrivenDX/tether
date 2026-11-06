import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const dom = new JSDOM('<div id="root"></div>', { url: "https://fabricated.test/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { TetherClient } = await import("@tether/client");
const { ClientProvider, useTetherClient } = await import("@tether/react");
const { SessionWorkspace } = await import("@tether/ui");
const requests = [];
const eventSignals = [];
const summary = {
  id: "fabricated", owner: "fabricated", title: "External embedder",
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  status: "idle", message_count: "1",
};
const client = new TetherClient({
  baseUrl: "https://fabricated.test/api/v1",
  fetch: async (input, init) => {
    const path = new URL(String(input)).pathname;
    requests.push(path);
    if (path.endsWith("/events")) {
      eventSignals.push(init?.signal);
      return new Response(new ReadableStream({
      start(controller) {
        init?.signal?.addEventListener("abort", () => {
          try { controller.close(); } catch { /* The reader may already have cancelled. */ }
        }, { once: true });
      },
      }), { headers: { "Content-Type": "text/event-stream" } });
    }
    const responses = {
      "/api/v1/capabilities": { methods: { confirm: "supported" }, tiers: { live: "available", archive: "unavailable" } },
      "/api/v1/sessions": { items: [summary], next: null },
      "/api/v1/sessions/fabricated/snapshot": { seq: "1", through_index: "0", view: { summary, tools: [], tools_status: "ready" } },
      "/api/v1/sessions/fabricated/pending": null,
      "/api/v1/sessions/fabricated/effects": { items: [], next_start: null },
      "/api/v1/sessions/fabricated/history": { items: [{ entry_index: "0", value: {
        status: "ready", ref: { key: "external", provider: "fabricated", sha256: "a".repeat(64) },
        record: { kind: "message", message: { role: "assistant", content: "Fabricated external view" } },
      } }], next_start: null },
    };
    assert.ok(Object.hasOwn(responses, path), "unexpected request from external view");
    return Response.json(responses[path]);
  },
});
function Layout({ navigation, content }) {
  return h("section", { "data-external-layout": true }, navigation, h("main", { id: "tether-main" }, content));
}
function Message({ message }) {
  return h("p", { "data-external-message": true }, `Custom: ${message.content}`);
}
function View() {
  assert.equal(useTetherClient(), client, "custom view did not receive its injected client");
  return h(SessionWorkspace, { selectedId: "fabricated", onSelect: () => undefined, slots: { Layout, Message } });
}
// @covers US-069-AC4: execute the installed public provider and custom view together.
const root = createRoot(document.getElementById("root"));
try {
  await act(async () => root.render(h(ClientProvider, { client }, h(View))));
  const deadline = performance.now() + 5000;
  while ((!document.querySelector("[data-external-message]") || eventSignals.length === 0) && performance.now() < deadline) {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
  assert.ok(document.querySelector("[data-external-layout]"));
  assert.equal(document.querySelector("[data-external-message]")?.textContent, "Custom: Fabricated external view");
  assert.ok(requests.includes("/api/v1/sessions/fabricated/snapshot"));
  assert.ok(requests.includes("/api/v1/sessions/fabricated/history"));
  assert.ok(requests.every((path) => path.startsWith("/api/v1/")));
} finally {
  await act(async () => root.unmount());
  dom.window.close();
  assert.ok(eventSignals.length > 0, "external view never opened its live feed");
  assert.ok(eventSignals.every((signal) => signal?.aborted), "unmounted external view retained a live feed");
  // This watchdog does not keep Node alive. Any product handle that survives
  // unmount must settle within five seconds or fail, including query GC timers.
  const deadline = setTimeout(() => { throw new Error("consumer_resources_remain_after_unmount"); }, 5000);
  deadline.unref();
  process.once("beforeExit", () => clearTimeout(deadline));
}
