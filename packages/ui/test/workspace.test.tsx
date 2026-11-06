import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TetherClient } from "@tether/client";
import { ClientProvider, useSessionFeed } from "@tether/react";
import { SessionWorkspace, DefaultMessage, type SessionLayoutProps, type MessageRendererProps } from "../src/index.js";
import { useState } from "react";

const summary = { id: "demo", owner: "fabricated", title: "Review a proposal", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", status: "waiting", message_count: "1" };
const pending = { actor_id: "fabricated", args_digest: "digest", arguments: { recipient: "fabricated@example.test" }, invocation_id: "approval", requested_at: "2026-01-01T00:00:00Z", session_id: "demo", spec_digest: "digest", tool: "send_note", tool_digest: "digest" };
function fixture(historyItems?: unknown[], options: { status?: string; replyText?: string } = {}) {
  let approved = false;
  const selectedSummary = { ...summary, status: options.status ?? summary.status };
  const requests: { path: string; body: unknown }[] = [];
  const transport = vi.fn<typeof fetch>(async (input, init) => {
    const path = new URL(String(input)).pathname;
    requests.push({ path, body: init?.body ? JSON.parse(String(init.body)) as unknown : null });
    if (path.endsWith("/events")) {
      const events = options.replyText === undefined ? "" : `event: reply_text\ndata: ${JSON.stringify({ type: "reply_text", session_id: "demo", turn_id: "turn-1", attempt_id: "attempt-1", offset: "0", text: options.replyText })}\n\n`;
      return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(events)); init?.signal?.addEventListener("abort", () => { try { controller.close(); } catch { /* already closed */ } }); } }), { headers: { "Content-Type": "text/event-stream" } });
    }
    const defaultHistoryItems = [{
      entry_index: "0",
      value: {
        status: "ready",
        ref: { key: "fixture", provider: "fabricated", sha256: "a".repeat(64) },
        record: { kind: "message", message: { role: "assistant", content: "<script>alert(1)</script>" } },
      },
    }];
    const handlers: Record<string, unknown> = {
      "/api/v1/capabilities": { methods: { confirm: "supported" }, tiers: { live: "available", archive: "unavailable" } },
      "/api/v1/sessions": { items: [selectedSummary], next: null },
      "/api/v1/sessions/demo/snapshot": { seq: "9007199254740993", through_index: "0", view: { summary: selectedSummary, pending: approved ? null : pending, tools: [], tools_status: "ready" } },
      "/api/v1/sessions/demo/history": { items: historyItems ?? defaultHistoryItems, next_start: null },
      "/api/v1/sessions/demo/pending": approved ? null : pending,
      "/api/v1/sessions/demo/effects": { items: [], next_start: null },
    };
    if (path.endsWith("/confirmations/approval")) { approved = true; return Response.json({ accepted: false, state: { summary } }); }
    if (!(path in handlers)) throw new Error(`unexpected request ${path}`);
    return Response.json(handlers[path]);
  });
  return { client: new TetherClient({ baseUrl: "https://fixture.test/api/v1", fetch: transport }), requests };
}
function archiveResponse(url: URL, method: string, restoreDenied: boolean, state: { restored: boolean }): Response {
  const path = url.pathname;
  const handlers: Record<string, () => Response> = {
    "/events": () => new Response(new ReadableStream(), { headers: { "Content-Type": "text/event-stream" } }),
    "/capabilities": () => Response.json({ methods: { restore: "supported" }, tiers: { live: "available", archive: "available" } }),
    "/sessions": () => Response.json({ items: state.restored || url.searchParams.get("tier") !== "archive" ? [{ ...summary, tier: "live" }] : [{ ...summary, tier: "archive" }], next: null }),
    "/snapshot": () => Response.json({ seq: "0", through_index: "0", view: { summary: { ...summary, tier: state.restored ? "live" : "archive" }, pending: null, tools: [], tools_status: "ready" } }),
    "/history": () => Response.json({ items: [{ entry_index: "0", value: { status: "ready", ref: { key: "archive", provider: "fabricated", sha256: "c".repeat(64) }, record: { kind: "message", message: { role: "assistant", content: "Archived answer" } } } }], next_start: null }),
    "/restore": () => restoreResponse(path, restoreDenied, state),
    "/pending": () => Response.json(null),
  };
  for (const [suffix, respond] of Object.entries(handlers)) if (path.endsWith(suffix)) return respond();
  throw new Error(`unexpected request ${path} ${method}`);
}
function restoreResponse(path: string, denied: boolean, state: { restored: boolean }): Response {
  if (denied) return Response.json({ type: "about:blank", title: "Forbidden", status: 403, detail: "Restore denied", instance: path }, { status: 403 });
  state.restored = true;
  return Response.json({ ...summary, tier: "live" });
}
function archiveFixture(restoreDenied: boolean) {
  const requests: { path: string; search: string; method: string }[] = [];
  const state = { restored: false };
  const transport = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    requests.push({ path: url.pathname, search: url.search, method });
    return archiveResponse(url, method, restoreDenied, state);
  });
  return { client: new TetherClient({ baseUrl: "https://fixture.test/api/v1", fetch: transport }), requests };
}
afterEach(cleanup);
describe("composable session presentation", () => {
  it("@covers US-067-AC4 renders hostile message markup as literal text", () => {
    const { container } = render(<DefaultMessage message={{ role: "assistant", content: "<script>alert(1)</script>" }} />);
    expect(screen.getByText("<script>alert(1)</script>")).toBeVisible();
    expect(container.querySelector("script")).toBeNull();
  });
  it("deduplicates complete history references but preserves distinct references with equal content", async () => {
    const repeatedRef = { key: "same", provider: "fabricated", sha256: "b".repeat(64) };
    const message = { role: "tool", content: "same user-visible result" };
    const historyItems = [
      { entry_index: "1", value: { status: "ready", ref: repeatedRef, record: { kind: "message", message } } },
      { entry_index: "2", value: { status: "ready", ref: repeatedRef, record: { kind: "message", message } } },
      { entry_index: "3", value: { status: "ready", ref: { ...repeatedRef, key: "different" }, record: { kind: "message", message } } },
    ];
    const { client } = fixture(historyItems);
    render(<ClientProvider client={client}><SessionWorkspace selectedId="demo" onSelect={() => undefined} /></ClientProvider>);
    expect(await screen.findAllByText("same user-visible result")).toHaveLength(2);
  });
  it("hides stale provisional text after a terminal snapshot while committed history is visible", async () => {
    const finalText = "The fabricated record is complete.";
    const historyItems = [{ entry_index: "1", value: { status: "ready", ref: { key: "committed", provider: "fabricated", sha256: "d".repeat(64) }, record: { kind: "message", message: { role: "assistant", content: finalText } } } }];
    const { client } = fixture(historyItems, { status: "idle", replyText: finalText });
    function FeedProbe() {
      const feed = useSessionFeed("demo");
      return <output data-testid="stale-feed">{feed.reply?.text ?? "empty"}</output>;
    }
    render(<ClientProvider client={client}><><SessionWorkspace selectedId="demo" onSelect={() => undefined} /><FeedProbe /></></ClientProvider>);
    expect(await screen.findByTestId("stale-feed")).toHaveTextContent(finalText);
    expect(await screen.findAllByText(finalText, { selector: ".tether-message-assistant p" })).toHaveLength(1);
    expect(screen.queryByRole("article", { name: "Provisional reply" })).toBeNull();
  });
  it("@covers US-067-AC2 @covers US-067-AC3 uses the injected instance and public layout/message slots", async () => {
    const { client, requests } = fixture();
    function Layout({ navigation, content }: SessionLayoutProps) { return <div data-testid="custom-layout">{navigation}<main id="tether-main">{content}</main></div>; }
    function Message({ message }: MessageRendererProps) { return <p>Custom: {message.content}</p>; }
    render(<ClientProvider client={client}><SessionWorkspace selectedId="demo" onSelect={() => undefined} slots={{ Layout, Message }} /></ClientProvider>);
    expect(await screen.findByText("Custom: <script>alert(1)</script>")).toBeVisible();
    expect(screen.getByTestId("custom-layout")).toBeVisible();
    expect(requests.some((item) => item.path.endsWith("/snapshot"))).toBe(true);
  });
  it.each([true, false])("@covers US-067-AC7 supports keyboard decision %s and retains accessible outcome focus", async (approved) => {
    const { client, requests } = fixture();
    const user = userEvent.setup();
    render(<ClientProvider client={client}><SessionWorkspace selectedId="demo" onSelect={() => undefined} /></ClientProvider>);
    const approve = await screen.findByRole("button", { name: approved ? "Approve" : "Decline" });
    approve.focus(); await user.keyboard("{Enter}");
    const result = await screen.findByText("Decision recorded. You can continue the conversation.");
    await waitFor(() => expect(result).toHaveFocus());
    expect(requests.find((item) => item.path.endsWith("/confirmations/approval"))?.body).toEqual({ answer: { approved } });
  });
  it.each([false, true])("loads selected archives as read-only and handles restore denial=%s", async (restoreDenied) => {
    const { client, requests } = archiveFixture(restoreDenied);
    function Harness() {
      const [selection, setSelection] = useState<{ id: string; tier: "live" | "archive" } | undefined>();
      return <SessionWorkspace selectedId={selection?.id} selectedTier={selection?.tier} onSelect={(id, tier) => setSelection({ id, tier })} />;
    }
    const user = userEvent.setup();
    render(<ClientProvider client={client}><Harness /></ClientProvider>);
    await user.click(await screen.findByRole("button", { name: "View archive" }));
    await user.click(await screen.findByRole("button", { name: /Review a proposal/ }));
    expect(await screen.findByText("Archived answer")).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Write a message" })).toBeNull();
    expect(requests.some((item) => item.path.endsWith("/snapshot") && item.search.includes("tier=archive"))).toBe(true);
    expect(requests.some((item) => item.path.endsWith("/history") && item.search.includes("tier=archive"))).toBe(true);
    expect(requests.some((item) => item.path.endsWith("/events"))).toBe(false);
    await user.click(await screen.findByText("Archive controls"));
    await user.click(await screen.findByRole("button", { name: "Restore archive" }));
    await waitFor(() => expect(requests.some((item) => item.path.endsWith("/restore"))).toBe(true));
    if (restoreDenied) {
      expect(await screen.findByRole("alert")).toHaveTextContent("http_error");
      expect(screen.getByRole("heading", { name: "Review a proposal" })).toBeVisible();
      expect(requests.some((item) => item.path.endsWith("/snapshot") && item.search.includes("tier=live"))).toBe(false);
    } else {
      await waitFor(() => expect(requests.some((item) => item.path.endsWith("/snapshot") && item.search.includes("tier=live"))).toBe(true));
    }
  });
});
