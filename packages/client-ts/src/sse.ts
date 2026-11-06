import { TetherError } from "./errors.js";

export interface SseFrame { event: string; data: string; id?: string }
export interface SseOptions { signal?: AbortSignal | undefined; maxFrameBytes?: number; idleMs?: number }

interface FrameFields { data: string[]; event: string; id?: string }
interface ParserState { buffer: string; lines: string[]; bytes: number }

function applyField(line: string, fields: FrameFields): void {
  if (line.startsWith(":")) return;
  const colon = line.indexOf(":" );
  const field = colon < 0 ? line : line.slice(0, colon);
  const rawValue = colon < 0 ? "" : line.slice(colon + 1);
  const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
  switch (field) {
    case "data": fields.data.push(value); break;
    case "event": fields.event = value; break;
    case "id": fields.id = eventId(fields.id, value); break;
  }
}

function eventId(previous: string | undefined, value: string): string {
  if (!/^(0|[1-9][0-9]*)$/.test(value) || (previous !== undefined && previous !== value)) {
    throw new TetherError("invalid_event_id");
  }
  return value;
}

function frame(lines: string[]): SseFrame | undefined {
  const fields: FrameFields = { data: [], event: "message" };
  for (const line of lines) applyField(line, fields);
  if (!fields.data.length) return undefined;
  const content = { event: fields.event, data: fields.data.join("\n") };
  return fields.id === undefined ? content : { ...content, id: fields.id };
}

async function readChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>, idleMs: number,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      reader.read(),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          void reader.cancel().catch(() => undefined);
          reject(new TetherError("stream_idle", { retryable: true }));
        }, idleMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

function consumeText(state: ParserState, text: string, maxBytes: number): SseFrame[] {
  state.buffer += text;
  const events: SseFrame[] = [];
  while (true) {
    const match = /\r\n|\r|\n/.exec(state.buffer);
    if (!match || (match[0] === "\r" && match.index === state.buffer.length - 1)) break;
    const line = state.buffer.slice(0, match.index);
    state.buffer = state.buffer.slice(match.index + match[0].length);
    state.bytes += new TextEncoder().encode(line).byteLength + 1;
    if (state.bytes > maxBytes) throw new TetherError("event_too_large");
    if (line) {
      state.lines.push(line);
      continue;
    }
    const parsed = frame(state.lines);
    state.lines = [];
    state.bytes = 0;
    if (parsed) events.push(parsed);
  }
  if (state.bytes + new TextEncoder().encode(state.buffer).byteLength > maxBytes) {
    throw new TetherError("event_too_large");
  }
  return events;
}

export async function* readSse(
  body: ReadableStream<Uint8Array>, options: SseOptions = {},
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const maxBytes = options.maxFrameBytes ?? 1024 * 1024;
  const state: ParserState = { buffer: "", lines: [], bytes: 0 };
  const cancel = () => { void reader.cancel().catch(() => undefined); };
  options.signal?.addEventListener("abort", cancel, { once: true });
  try {
    while (!options.signal?.aborted) {
      const result = await readChunk(reader, options.idleMs ?? 45000);
      if (result.done) break;
      const events = consumeText(state, decoder.decode(result.value, { stream: true }), maxBytes);
      for (const event of events) yield event;
    }
    decoder.decode();
  } finally {
    options.signal?.removeEventListener("abort", cancel);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
