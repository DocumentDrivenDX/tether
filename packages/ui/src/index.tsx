import type { SessionSlots } from "./presentation.js";
import { DefaultLayout } from "./presentation.js";
import { SessionList, type SessionTier } from "./navigation.js";
import { SessionView } from "./session.js";
export { SessionList } from "./navigation.js";
export { SessionView } from "./session.js";
export { Composer, Confirmation, UnknownEffects } from "./actions.js";
export { DefaultLayout, DefaultMessage, PresentationBoundary } from "./presentation.js";
export type { SessionSlots, SessionLayoutProps, MessageRendererProps } from "./presentation.js";
export interface SessionWorkspaceProps {
  selectedId?: string | undefined;
  selectedTier?: SessionTier | undefined;
  onSelect: (id: string, tier: SessionTier) => void;
  slots?: SessionSlots;
}
export function SessionWorkspace({ selectedId, selectedTier = "live", onSelect, slots = {} }: SessionWorkspaceProps) {
  const Layout = slots.Layout ?? DefaultLayout;
  return <div className="tether"><a className="tether-skip" href="#tether-main">Skip to conversation</a><Layout navigation={<SessionList selectedId={selectedId} selectedTier={selectedTier} onSelect={onSelect} />} content={selectedId ? <SessionView key={`${selectedId}:${selectedTier}`} id={selectedId} tier={selectedTier} onSelect={onSelect} messageRenderer={slots.Message} /> : <div className="tether-welcome"><p className="tether-eyebrow">A clear place to think</p><h2>Where will your<br />next thought lead?</h2><p>Create a session or return to a conversation.<br />Everything you need stays connected.</p></div>} /></div>;
}
