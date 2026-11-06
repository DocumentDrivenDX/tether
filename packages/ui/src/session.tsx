import { useEffect, useRef, useState, type ComponentType } from "react";
import type { Message, SessionSummary, HistoryPage, SessionSnapshot } from "@tether/client";
import { useCapabilities, useCommand, useHistory, usePending, useSession, useSessionFeed, useTetherClient } from "@tether/react";
import { Composer, Confirmation, UnknownEffects } from "./actions.js";
import { DefaultMessage, Failure, PresentationBoundary, type MessageRendererProps } from "./presentation.js";
import type { SessionTier } from "./navigation.js";

function SessionControls({ id, session, onSelect }: { id: string; session: SessionSummary; onSelect: (id: string, tier: SessionTier) => void }) {
  const client = useTetherClient();
  const command = useCommand(id);
  const [title, setTitle] = useState(session.title ?? "");
  if (session.tier === "archive") return <details className="tether-controls"><summary>Archive controls</summary><LifecycleControl id={id} session={session} command={command} onSelect={onSelect} />{command.isError && <Failure error={command.error} />}</details>;
  return <details className="tether-controls"><summary>Session controls</summary><form onSubmit={(event) => { event.preventDefault(); command.mutate(() => client.update(id, { title })); }}><label htmlFor="tether-title">Session title</label><input id="tether-title" value={title} onChange={(event) => setTitle(event.target.value)} /><button disabled={command.isPending}>Save title</button></form><div className="tether-actions">
    {session.active_turn_id && <button disabled={command.isPending} onClick={() => command.mutate(() => client.cancelTurn(id, session.active_turn_id!))}>Cancel active turn</button>}
    {session.resumable && <button disabled={command.isPending} onClick={() => command.mutate(() => client.resume(id))}>Resume session</button>}
    <LifecycleControl id={id} session={session} command={command} onSelect={onSelect} />
  </div>{command.isError && <Failure error={command.error} />}{command.isSuccess && <p role="status">Session updated.</p>}</details>;
}
function LifecycleControl({ id, session, command, onSelect }: { id: string; session: SessionSummary; command: ReturnType<typeof useCommand>; onSelect: (id: string, tier: SessionTier) => void }) {
  const client = useTetherClient();
  const capabilities = useCapabilities();
  if (session.tier === "archive") {
    return capabilities.data?.methods.restore === "supported" ? <button disabled={command.isPending} onClick={() => command.mutate(() => client.restore(id), { onSuccess: () => onSelect(id, "live") })}>Restore archive</button> : <p>Archive restore is unavailable for this connection.</p>;
  }
  if (session.status === "closed") return <p>This session is closed. Create a new session to continue.</p>;
  return <button disabled={command.isPending} onClick={() => command.mutate(() => client.close(id))}>Close session</button>;
}
function Transcript({ id, tier, renderer: Renderer }: { id: string; tier: SessionTier; renderer: ComponentType<MessageRendererProps> }) {
  const [positions, setPositions] = useState<bigint[]>([0n]);
  const start = positions.at(-1) ?? 0n;
  const query = useHistory(id, start, tier);
  return <section aria-label="Conversation" className="tether-transcript">
    {query.isPending && <p role="status">Loading conversation…</p>}{query.isError && <Failure error={query.error} />}
    {query.data?.partial && <p role="status">Some history is unavailable. The available records are shown below.</p>}
    {query.data?.items.length === 0 && <div className="tether-empty"><span aria-hidden="true">↗</span><h2>Start with a thought.</h2><p>Ask a question, explore an idea, or pick up your work.</p></div>}
    {query.data && <HistoryRecords data={query.data} renderer={Renderer} />}
    {query.data && <HistoryNavigation data={query.data} positions={positions} onPositions={setPositions} />}
  </section>;
}
function HistoryRecords({ data, renderer: Renderer }: { data: HistoryPage; renderer: ComponentType<MessageRendererProps> }) {
  return <>{uniqueHistorySlots(data.items).map((slot) => {
    const message: Message | null | undefined = slot.value.record?.message;
    return message ? <PresentationBoundary key={slot.entry_index.toString()}><Renderer message={message} /></PresentationBoundary> : <p key={slot.entry_index.toString()}>History {slot.entry_index.toString()}: {slot.value.status} · {slot.value.reason}</p>;
  })}</>;
}
function uniqueHistorySlots(items: HistoryPage["items"]): HistoryPage["items"] {
  const seen = new Set<string>();
  return items.filter((slot) => {
    const identity = JSON.stringify(Object.entries(slot.value.ref).sort(([left], [right]) => left.localeCompare(right)));
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}
function HistoryNavigation({ data, positions, onPositions }: { data: HistoryPage; positions: bigint[]; onPositions: (positions: bigint[]) => void }) {
  const next = data.next_start;
  return <nav aria-label="Conversation pages" className="tether-actions">{positions.length > 1 && <button onClick={() => onPositions(positions.slice(0, -1))}>Previous messages</button>}{next !== null && <button onClick={() => onPositions([...positions, next])}>Next messages</button>}</nav>;
}
export function SessionView({ id, tier = "live", onSelect, messageRenderer = DefaultMessage }: { id: string; tier?: SessionTier | undefined; onSelect: (id: string, tier: SessionTier) => void; messageRenderer?: ComponentType<MessageRendererProps> | undefined }) {
  const query = useSession(id, tier);
  const pending = usePending(id, tier);
  const feed = useSessionFeed(id, tier);
  if (query.isPending) return <p role="status">Opening session…</p>;
  if (query.isError) return <Failure error={query.error} />;
  return <SessionContent id={id} tier={tier} data={query.data} messageRenderer={messageRenderer} pending={pending} feed={feed} onSelect={onSelect} />;
}
function SessionContent({ id, tier, data, messageRenderer, pending, feed, onSelect }: {
  id: string; tier: SessionTier; data: SessionSnapshot; messageRenderer: ComponentType<MessageRendererProps>;
  pending: ReturnType<typeof usePending>; feed: ReturnType<typeof useSessionFeed>;
  onSelect: (id: string, tier: SessionTier) => void;
}) {
  const [decision, setDecision] = useState(0);
  const status = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (decision) status.current?.focus(); }, [decision]);
  const view = data.view;
  return <div className="tether-session"><header className="tether-session-header"><div><p className="tether-eyebrow">Workspace / Conversation</p><h2>{view.summary.title || "Untitled session"}</h2><p role="status">{view.summary.status} · {feedLabel(feed.state)}</p></div><SessionControls id={id} session={view.summary} onSelect={onSelect} /></header>
    {feed.state === "error" && <div role="alert">Connection interrupted. Your saved conversation is retained. <button onClick={feed.reconnect}>Reconnect</button></div>}
    <p role="status" tabIndex={-1} ref={status}>{decision ? "Decision recorded. You can continue the conversation." : ""}</p>
    <Transcript key={`transcript-${id}:${tier}`} id={id} tier={tier} renderer={messageRenderer} />
    {tier === "live" && <LiveSessionTools id={id} summary={view.summary} tools={view.tools} toolsStatus={view.tools_status} pending={pending} feed={feed} onDecision={() => setDecision((value) => value + 1)} />}
  </div>;
}

function LiveSessionTools({ id, summary, tools, toolsStatus, pending, feed, onDecision }: {
  id: string; summary: SessionSummary; tools: SessionSnapshot["view"]["tools"];
  toolsStatus: SessionSnapshot["view"]["tools_status"];
  pending: ReturnType<typeof usePending>; feed: ReturnType<typeof useSessionFeed>;
  onDecision: () => void;
}) {
  return <>
    {summary.status === "running" && feed.reply && <article className="tether-message" aria-label="Provisional reply"><h3>Assistant · composing</h3><p>{feed.reply.text}</p></article>}
    {pending.isError && <Failure error={pending.error} />}{pending.data && <Confirmation key={pending.data.invocation_id} id={id} pending={pending.data} onDecision={onDecision} />}
    <UnknownEffects id={id} /><Composer key={`composer-${id}`} id={id} disabled={composerDisabled(summary, Boolean(pending.data))} />
    <details className="tether-tools"><summary>Available tools</summary>{toolsStatus !== "ready" && <p>Tool details {toolsStatus ?? "not loaded"}.</p>}{tools?.map((tool) => <div key={tool.name}><h3>{tool.name}</h3><p>{tool.description}</p></div>)}</details>
  </>;
}

function composerDisabled(summary: SessionSummary, pending: boolean): boolean { return summary.status === "closed" || summary.status === "running" || pending; }

function feedLabel(state: string): string { return state === "live" ? "Connected" : state; }
