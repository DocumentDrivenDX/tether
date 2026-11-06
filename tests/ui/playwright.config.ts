import { defineConfig } from "@playwright/test";
import { createServer } from "node:net";
import { MAX_FUNCTIONAL_WATCHDOG_MS } from "./functional-watchdog.js";

const functionalWatchdog = MAX_FUNCTIONAL_WATCHDOG_MS;

async function selectedLoopbackPort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("browser_fixture_port_unavailable");
  }
  const port = address.port;
  await new Promise<void>((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
  return port;
}

const invocationPortVariable = "TETHER_BROWSER_INVOCATION_PORT";
const inheritedPort = process.env[invocationPortVariable];
const browserPort = inheritedPort === undefined
  ? await selectedLoopbackPort()
  : Number(inheritedPort);
if (!Number.isInteger(browserPort) || browserPort < 1 || browserPort > 65535) {
  throw new Error("browser_fixture_port_invalid");
}
process.env[invocationPortVariable] = String(browserPort);
const browserOrigin = `http://127.0.0.1:${browserPort}`;

export default defineConfig({
  testDir: ".", testMatch: "reference.spec.ts", timeout: functionalWatchdog, workers: 1,
  expect: { timeout: functionalWatchdog },
  outputDir: "../../artifacts/browser-results", reporter: [["list"], ["json", { outputFile: new URL("../../artifacts/browser-report.json", import.meta.url).pathname }], new URL("./playwright-timeout-reporter.mjs", import.meta.url).pathname],
  use: {
    baseURL: browserOrigin,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    actionTimeout: functionalWatchdog,
    navigationTimeout: functionalWatchdog,
  },
  webServer: {
    command: `${JSON.stringify(process.env.TETHER_BROWSER_PYTHON ?? ".venv/bin/python")} tests/ui/server.py`, cwd: new URL("../..", import.meta.url).pathname, url: `${browserOrigin}/assistant/`,
    env: { ...process.env, TETHER_BROWSER_PORT: String(browserPort) },
    reuseExistingServer: false, timeout: functionalWatchdog,
  },
});
