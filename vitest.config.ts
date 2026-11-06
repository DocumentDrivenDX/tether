import { defineConfig } from "vitest/config";
import { MAX_FUNCTIONAL_WATCHDOG_MS } from "./tests/ui/functional-watchdog.js";

export default defineConfig({ test: {
  include: ["packages/*/test/**/*.test.{ts,tsx}"], environment: "jsdom",
  reporters: ["default", "json"],
  outputFile: { json: "./artifacts/vitest-report.json" },
  restoreMocks: true, setupFiles: ["./tests/ui/setup.ts"],
  testTimeout: 0,
  hookTimeout: 0,
  teardownTimeout: MAX_FUNCTIONAL_WATCHDOG_MS,
} });
