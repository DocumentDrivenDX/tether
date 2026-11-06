import { TetherError } from "./errors.js";

import { COUNTER_NAMES } from "./wire/counters.js";

export const counterKeys = new Set<string>(COUNTER_NAMES);
const opaqueKeys = new Set([
  "settings", "arguments", "facts", "request", "response", "parameters",
  "schema", "metadata", "data", "source_locator",
]);
type CounterKey = (typeof COUNTER_NAMES)[number];
type OpaqueKey = "settings" | "arguments" | "facts" | "request" | "response"
  | "parameters" | "schema" | "metadata" | "data" | "source_locator";
export type Exact<T> = T extends readonly (infer U)[] ? Exact<U>[]
  : T extends object ? { [K in keyof T]: K extends OpaqueKey ? T[K]
    : K extends CounterKey ? bigint | Extract<T[K], null | undefined> : Exact<T[K]> }
  : T;

function counter(value: unknown, encode: boolean): unknown {
  if (value === null || value === undefined) return value;
  if (encode && typeof value === "bigint") return value.toString();
  if (!encode && typeof value === "string" && /^-?(0|[1-9][0-9]*)$/.test(value)) {
    return BigInt(value);
  }
  throw new TetherError("invalid_counter");
}

function transform(value: unknown, encode: boolean): unknown {
  if (Array.isArray(value)) return value.map((item: unknown) => transform(item, encode));
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]: [string, unknown]) => {
    if (opaqueKeys.has(key)) return [key, item];
    return [key, counterKeys.has(key) ? counter(item, encode) : transform(item, encode)];
  }));
}

export function hydrate<T>(value: T): Exact<T> {
  return transform(value, false) as Exact<T>;
}
export function serialize(value: unknown): string {
  return JSON.stringify(transform(value, true));
}
