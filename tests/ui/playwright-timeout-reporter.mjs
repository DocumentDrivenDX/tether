import { mkdirSync, unlinkSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const TIMEOUT_MARKER = resolve("artifacts/playwright-incomplete.json");

export default class FunctionalTimeoutReporter {
  constructor() {
    this.frameworkTimedOut = false;
    try {
      unlinkSync(TIMEOUT_MARKER);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  onTestEnd(_test, result) {
    this.frameworkTimedOut ||= result.status === "timedOut";
  }

  onEnd(result) {
    if (result.status !== "timedout" && !this.frameworkTimedOut) return;
    mkdirSync(resolve("artifacts"), { recursive: true });
    writeFileSync(
      TIMEOUT_MARKER,
      JSON.stringify({
        schema_version: 1,
        status: "incomplete",
        framework: "playwright",
        reason: "playwright_timedout",
        phase: "execution",
      }),
      { mode: 0o600 },
    );
    return { status: "timedout" };
  }
}
