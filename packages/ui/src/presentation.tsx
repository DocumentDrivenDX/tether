import { Component, type ComponentType, type ReactNode } from "react";
import type { Message, SessionSummary } from "@tether/client";

export interface MessageRendererProps { message: Message }
export interface SessionLayoutProps { navigation: ReactNode; content: ReactNode }
export interface SessionSlots {
  Layout?: ComponentType<SessionLayoutProps>;
  Message?: ComponentType<MessageRendererProps>;
}
export function DefaultMessage({ message }: MessageRendererProps) {
  return <article className={`tether-message tether-message-${message.role}`}>
    <h3>{message.role}</h3><p>{message.content}</p>
    {message.attachments?.map((item) => <p key={item.id}>Attachment: {item.filename} ({item.media_type}){item.withheld ? " · withheld" : ""}</p>)}
    {message.tool_calls?.map((call) => <details key={call.id}><summary>Tool: {call.name}</summary><pre>{JSON.stringify(call.arguments, null, 2)}</pre></details>)}
  </article>;
}
export function DefaultLayout({ navigation, content }: SessionLayoutProps) {
  return <div className="tether-workspace"><aside aria-label="Session navigation">{navigation}</aside><main id="tether-main">{content}</main></div>;
}
export function SessionLabel({ session }: { session: SessionSummary }) {
  return <><strong>{session.title || "Untitled session"}</strong><span>{session.status} · {session.message_count?.toString() ?? "0"} messages</span></>;
}
export function Failure({ error }: { error: unknown }) {
  return <p role="alert">{error instanceof Error ? error.message : "The request could not be completed."}</p>;
}
export class PresentationBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="alert">The custom presentation failed. Reload this view to try again.</p> : this.props.children; }
}
