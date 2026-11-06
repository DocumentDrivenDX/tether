import { defineConfig } from "@playwright/test";

const appUrl = process.env.TETHER_LIVE_APP_URL;
const authorization = process.env.TETHER_LIVE_AUTHORIZATION;
const apiPrefix = process.env.TETHER_LIVE_API_PREFIX ?? "/api/v1";
const mountPath = process.env.TETHER_LIVE_MOUNT_PATH ?? "";
const reportPath = process.env.TETHER_LIVE_JSON_REPORT;

if (!appUrl || !authorization || !authorization.startsWith("Bearer ") || !reportPath) {
  throw new Error("live_profile_configuration_missing");
}

export default defineConfig({
  testDir: ".",
  testMatch: "databricks-live.spec.ts",
  timeout: 0,
  expect: { timeout: 0 },
  maxFailures: 1,
  workers: 1,
  reporter: [["json", { outputFile: reportPath }]],
  outputDir: process.env.TETHER_LIVE_PRIVATE_OUTPUT,
  use: {
    baseURL: appUrl + mountPath + "/",
    trace: "off",
    screenshot: "off",
    video: "off",
    actionTimeout: 0,
    navigationTimeout: 0,
  },
  metadata: { apiPrefix, mountPath },
});
