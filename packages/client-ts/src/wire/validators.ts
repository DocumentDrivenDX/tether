import { validators } from "./validators.generated.js";
import type { TetherWire } from "./generated.js";
import { hydrate, type Exact } from "../exact.js";
import { TetherError } from "../errors.js";

export function decode<K extends keyof TetherWire>(name: K, value: unknown): Exact<TetherWire[K]> {
  const check = validators[name];
  if (!check || !check(value)) throw new TetherError("invalid_response");
  return hydrate(value as TetherWire[K]);
}
