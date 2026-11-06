import { useEffect, useRef, useState, type FormEvent } from "react";
import { useCapabilities, useSessions, useTetherClient } from "@tether/react";
import { Failure, SessionLabel } from "./presentation.js";

export type SessionTier = "live" | "archive";
export interface SessionListProps { selectedId?: string | undefined; selectedTier?: SessionTier | undefined; onSelect: (id: string, tier: SessionTier) => void }
export function SessionList({ selectedId, selectedTier, onSelect }: SessionListProps) {
  const [tier, setTier] = useState<"live" | "archive">("live");
  const requestId = useRef<string | null>(null);
  const [text, setText] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const client = useTetherClient();
  useEffect(() => {
    if (!selectedTier) return;
    setTier(selectedTier);
    setCursor(undefined);
  }, [selectedTier]);
  const query = useSessions({ tier, text, ...(cursor ? { cursor } : {}), limit: 30 });
  const create = async () => {
    setCreating(true); setError(null);
    try { requestId.current ??= crypto.randomUUID(); const session = await client.create({ request_id: requestId.current }); requestId.current = null; await query.refetch(); onSelect(session.id, "live"); }
    catch (cause) { setError(cause); }
    finally { setCreating(false); }
  };
  const search = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setCursor(undefined); };
  return <section><header className="tether-brand"><span aria-hidden="true">↗</span><div><h1>Tether</h1><p>Your work, connected</p></div></header>
    <button className="tether-primary" disabled={creating} onClick={() => { void create(); }}>{creating ? "Creating…" : "+ New session"}</button>
    <form onSubmit={search}><label htmlFor="tether-search">Find a session</label><input id="tether-search" type="search" value={text} onChange={(event) => { setText(event.target.value); setCursor(undefined); }} /></form>
    <ArchiveControl tier={tier} onTier={(value) => { setTier(value); setCursor(undefined); }} />
    <h2>Sessions</h2>{query.isPending && <p role="status">Loading sessions…</p>}
    {query.isError && <Failure error={query.error} />}{error !== null && <Failure error={error} />}
    {query.data?.items.length === 0 && <p>No sessions yet. Start a conversation above.</p>}
    <nav aria-label="Sessions"><ul className="tether-session-list">{query.data?.items.map((session) => <li key={session.id}><button aria-current={session.id === selectedId && tier === selectedTier ? "page" : undefined} onClick={() => onSelect(session.id, tier)}><SessionLabel session={session} /></button></li>)}</ul></nav>
    <PageControls next={query.data?.next} cursor={cursor} onCursor={setCursor} />
  </section>;
}

function PageControls({ next, cursor, onCursor }: { next?: string | null | undefined; cursor: string | undefined; onCursor: (cursor: string | undefined) => void }) {
  return <>{next && <button onClick={() => onCursor(next)}>Next sessions</button>}{cursor && <button onClick={() => onCursor(undefined)}>First sessions</button>}</>;
}

function ArchiveControl({ tier, onTier }: { tier: "live" | "archive"; onTier: (tier: "live" | "archive") => void }) {
  const query = useCapabilities();
  const available = query.data?.tiers.archive === "available";
  return <div><button disabled={!available} aria-pressed={tier === "archive"} onClick={() => onTier(tier === "live" ? "archive" : "live")}>{tier === "live" ? "View archive" : "View live sessions"}</button>{!available && <p>Archive unavailable for this connection.</p>}</div>;
}
