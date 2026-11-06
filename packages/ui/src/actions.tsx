import { useRef, useState, type FormEvent } from "react";
import type { PendingConfirmation } from "@tether/client";
import { useCapabilities, useCommand, useEffects, useTetherClient } from "@tether/react";
import { Failure } from "./presentation.js";

export function Composer({ id, disabled }: { id: string; disabled: boolean }) {
  const [text, setText] = useState("");
  const requestId = useRef<string | null>(null);
  const client = useTetherClient();
  const command = useCommand(id);
  const submit = (event: FormEvent) => {
    event.preventDefault(); if (!text.trim() || disabled || command.isPending) return;
    requestId.current ??= crypto.randomUUID();
    command.mutate(async () => { await client.send(id, { request_id: requestId.current!, text }); requestId.current = null; setText(""); });
  };
  return <form className="tether-composer" onSubmit={submit}>
    <label htmlFor="tether-message">Message</label><textarea id="tether-message" value={text} onChange={(event) => { setText(event.target.value); requestId.current = null; }} disabled={disabled || command.isPending} placeholder="What would you like to work on?" rows={3} />
    <div><span>Enter text, then send. Your session stays available across interfaces.</span><button className="tether-primary" disabled={disabled || command.isPending || !text.trim()}> {command.isPending ? "Sending…" : "Send message"}</button></div>
    {command.isError && <Failure error={command.error} />}
    {command.isSuccess && <p role="status">Message accepted.</p>}
  </form>;
}
export function Confirmation({ id, pending, onDecision }: { id: string; pending: PendingConfirmation; onDecision?: (() => void) | undefined }) {
  const client = useTetherClient();
  const command = useCommand(id);
  const [note, setNote] = useState("");
  const outcome = useRef<HTMLParagraphElement>(null);
  const capabilities = useCapabilities();
  const supported = capabilities.data?.methods.confirm === "supported";
  const disabled = command.isPending || !supported;
  const answer = (approved: boolean) => {
    const trimmedNote = note.trim();
    const decision = { approved, ...(trimmedNote ? { note } : {}) };
    command.mutate(() => client.confirm(id, pending.invocation_id, { answer: decision }), { onSuccess: () => onDecision?.(), onSettled: () => outcome.current?.focus() });
  };
  return <section className="tether-confirmation" aria-labelledby="tether-confirm-title"><h3 id="tether-confirm-title">Permission requested: {pending.tool}</h3><p>{pending.description || pending.reason}</p>
    <pre aria-label="Requested arguments">{JSON.stringify(pending.arguments, null, 2)}</pre><label htmlFor="tether-note">Decision note</label><input id="tether-note" value={note} onChange={(event) => setNote(event.target.value)} />
    <div className="tether-actions"><button disabled={disabled} onClick={() => answer(false)}>Decline</button><button className="tether-primary" disabled={disabled} onClick={() => answer(true)}>Approve</button></div>
    {!supported && <p>Approval decisions are unavailable for this connection.</p>}
    <p tabIndex={-1} ref={outcome} role="status">{command.isPending ? "Recording decision…" : command.isSuccess ? "Decision recorded." : "Review the tool and arguments before deciding."}</p>
    {command.isError && <Failure error={command.error} />}
  </section>;
}
function EffectResolution({ id, invocation, tool }: { id: string; invocation: string; tool: string }) {
  const [reason, setReason] = useState("");
  const client = useTetherClient();
  const command = useCommand(id);
  return <fieldset><legend>{tool}: outcome unknown</legend><p>Verify the external system before recording an outcome. This records an operator decision.</p><label htmlFor={`reason-${invocation}`}>Evidence and reason</label><input id={`reason-${invocation}`} value={reason} onChange={(event) => setReason(event.target.value)} />
    <div className="tether-actions">{(["completed", "not_completed"] as const).map((outcome) => <button key={outcome} disabled={!reason.trim() || command.isPending} onClick={() => command.mutate(() => client.resolve(id, invocation, { outcome, reason }))}>Record {outcome.replaceAll("_", " ")}</button>)}</div>
    {command.isError && <Failure error={command.error} />}{command.isSuccess && <p role="status">Outcome recorded.</p>}</fieldset>;
}
export function UnknownEffects({ id }: { id: string }) {
  const query = useEffects(id);
  return <section aria-label="Unknown effects">{query.isError && <Failure error={query.error} />}{query.data?.items.map((effect) => <EffectResolution key={effect.invocation_id} id={id} invocation={effect.invocation_id} tool={effect.tool} />)}</section>;
}
