import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const control = process.env.TETHER_CONSUMER_VIEW_CONTROL ?? "baseline";
assert.ok(["baseline", "delayed", "failed-fetch"].includes(control), "consumer control mode invalid");
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
let resolveFeedRead;
let resolveFeedPullRequested;
let resolveProducerFailure;
let releaseFeedPull;
let releaseHistory;
let unmountStarted = false;
let feedReadObserved = false;
const feedPullGate = control === "delayed" ? new Promise((resolve) => { releaseFeedPull = resolve; }) : Promise.resolve();
const historyGate = control === "delayed" ? new Promise((resolve) => { releaseHistory = resolve; }) : Promise.resolve();
const feedRead = new Promise((resolve) => { resolveFeedRead = resolve; });
const feedPullRequested = new Promise((resolve) => { resolveFeedPullRequested = resolve; });
let resolveHistoryRequested;
const historyRequested = new Promise((resolve) => { resolveHistoryRequested = resolve; });
const producerFailure = new Promise((_, reject) => { resolveProducerFailure = reject; });
// The package owner observes child exit; this rejection channel reports an
// unexpected producer failure while the consumer's required state is pending.
producerFailure.catch(() => undefined);
const summary = {
  id: "fabricated", owner: "fabricated", title: "External embedder",
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  status: "idle", message_count: "1",
};
const failedFetchSentinel = new Error("fabricated_history_fetch_failure");
const client = new TetherClient({
  baseUrl: "https://fabricated.test/api/v1",
  fetch: async (input, init) => {
    try {
      const path = new URL(String(input)).pathname;
      requests.push(path);
      if (path.endsWith("/events")) {
        eventSignals.push(init?.signal);
        return new Response(new ReadableStream({
          start(controller) {
            init?.signal?.addEventListener("abort", () => {
              if (!unmountStarted) resolveProducerFailure(new Error("event feed aborted before consumer unmount"));
              try { controller.close(); } catch { /* The reader may already have cancelled. */ }
            }, { once: true });
          },
          async pull() {
            resolveFeedPullRequested();
            await feedPullGate;
            feedReadObserved = true;
            resolveFeedRead();
          },
          cancel(reason) {
            if (!unmountStarted) {
              resolveProducerFailure(reason instanceof Error ? reason : new Error("event feed ended before consumer unmount"));
            }
          },
        }, { highWaterMark: 0 }), { headers: { "Content-Type": "text/event-stream" } });
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
      if (path.endsWith("/history")) {
        resolveHistoryRequested();
        await historyGate;
        if (control === "failed-fetch") {
          await feedRead;
          throw failedFetchSentinel;
        }
      }
      assert.ok(Object.hasOwn(responses, path), "unexpected request from external view");
      return Response.json(responses[path]);
    } catch (error) {
      resolveProducerFailure(error);
      throw error;
    }
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
const root = createRoot(document.getElementById("root"), {
  onCaughtError(error) { resolveProducerFailure(error); },
  onUncaughtError(error) { resolveProducerFailure(error); },
  onRecoverableError(error) { resolveProducerFailure(error); },
});
let resolveRendered;
const rendered = new Promise((resolve) => { resolveRendered = resolve; });
const observer = new dom.window.MutationObserver(() => {
  if (
    document.querySelector("[data-external-layout]") &&
    document.querySelector("[data-external-message]")?.textContent === "Custom: Fabricated external view"
  ) {
    observer.disconnect();
    resolveRendered();
  }
});
observer.observe(document.getElementById("root"), { childList: true, characterData: true, subtree: true });
if (
  document.querySelector("[data-external-layout]") &&
  document.querySelector("[data-external-message]")?.textContent === "Custom: Fabricated external view"
) {
  observer.disconnect();
  resolveRendered();
}
async function waitForRequiredState() {
  let settled = false;
  let failure;
  Promise.race([Promise.all([rendered, feedRead]), producerFailure]).then(
    () => { settled = true; },
    (error) => { failure = error; settled = true; },
  );
  while (!settled) {
    await act(async () => new Promise((resolve) => setImmediate(resolve)));
  }
  if (failure) throw failure;
}
let consumerFailure;
try {
  await act(async () => root.render(h(ClientProvider, { client }, h(View))));
  if (control === "delayed") {
    await Promise.race([Promise.all([historyRequested, feedPullRequested]), producerFailure]);
    assert.equal(document.querySelector("[data-external-message]"), null, "delayed consumer rendered before its history response");
    assert.equal(feedReadObserved, false, "delayed feed was observed before its pull gate opened");
    releaseFeedPull();
    releaseHistory();
  }
  await waitForRequiredState();
  assert.ok(document.querySelector("[data-external-layout]"));
  assert.equal(document.querySelector("[data-external-message]")?.textContent, "Custom: Fabricated external view");
  assert.ok(requests.includes("/api/v1/sessions/fabricated/snapshot"));
  assert.ok(requests.includes("/api/v1/sessions/fabricated/history"));
  assert.ok(requests.every((path) => path.startsWith("/api/v1/")));
} catch (error) {
  consumerFailure = error;
} finally {
  const cleanupFailures = [];
  releaseFeedPull?.();
  releaseHistory?.();
  unmountStarted = true;
  try {
    await act(async () => root.unmount());
  } catch (error) {
    cleanupFailures.push(error);
  }
  try { observer.disconnect(); } catch (error) { cleanupFailures.push(error); }
  try { dom.window.close(); } catch (error) { cleanupFailures.push(error); }
  try {
    assert.ok(eventSignals.length > 0, "external view never opened its live feed");
    assert.ok(eventSignals.every((signal) => signal?.aborted), "unmounted external view retained a live feed");
  } catch (error) {
    cleanupFailures.push(error);
  }
  // The enclosing packages command owns execution and cleanup watchdogs; this
  // child must exit naturally so its real remaining handles are observed.
  if (control === "failed-fetch") {
    if (consumerFailure !== failedFetchSentinel) {
      cleanupFailures.unshift(consumerFailure ?? new Error("expected history fetch failure did not occur"));
    }
  } else if (consumerFailure) {
    cleanupFailures.unshift(consumerFailure);
  }
  if (cleanupFailures.length === 1) throw cleanupFailures[0];
  if (cleanupFailures.length > 1) throw new AggregateError(cleanupFailures, "consumer_failed_and_cleanup_failed");
}
