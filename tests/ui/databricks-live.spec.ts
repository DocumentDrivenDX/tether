import { test, expect } from "@playwright/test";
import { chmod, writeFile, access, link, unlink, rename } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import { join } from "node:path";

const rendezvous = process.env.TETHER_LIVE_RENDEZVOUS;
const apiPrefix = process.env.TETHER_LIVE_API_PREFIX ?? "/api/v1";
const appOrigin = new URL(process.env.TETHER_LIVE_APP_URL as string).origin;
const authorization = process.env.TETHER_LIVE_AUTHORIZATION as string;
const serviceCaller = process.env.TETHER_LIVE_SERVICE_CALLER === "1";
const require = createRequire(import.meta.url);
const playwrightVersion = (require("@playwright/test/package.json") as { version: string }).version;
type NetworkEvent = { method: string; route: string; status: number };
const networkByPage = new WeakMap<object, { scenario: string; requests: NetworkEvent[] }>();

if (!rendezvous) throw new Error("live_rendezvous_missing");
if (!authorization.startsWith("Bearer ")) throw new Error("live_authorization_missing");

test.beforeEach(async ({ page, browser }) => {
  await replacePrivate("browser-runtime.json", {
    chromium: browser.version(),
    playwright: playwrightVersion,
  });
  const network: NetworkEvent[] = [];
  const scenario = test.info().title.includes("AC8") ? "unknown" : "shared";
  networkByPage.set(page, { scenario, requests: network });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin !== appOrigin || !url.pathname.startsWith(apiPrefix)) return;
    const tail = url.pathname.slice(apiPrefix.length);
    const route = tail === "/capabilities" ? "capabilities"
      : tail === "/sessions" ? "sessions"
        : /^\/sessions\/[^/]+\/pending$/.test(tail) ? "pending"
          : /^\/sessions\/[^/]+\/confirmations\/[^/]+$/.test(tail) ? "confirmation"
            : /^\/sessions\/[^/]+\/turns$/.test(tail) ? "turns"
              : /^\/sessions\/[^/]+\/effects$/.test(tail) ? "effects"
                : "other";
    network.push({ method: response.request().method(), route, status: response.status() });
  });
  await page.route("**/*", (route) => {
    const request = route.request();
    if (new URL(request.url()).origin !== appOrigin) {
      return route.continue();
    }
    const headers: Record<string, string> = { ...request.headers(), authorization };
    if (serviceCaller) headers["x-tether-service-caller-authorization"] = authorization;
    return route.continue({ headers });
  });
});

test.afterEach(async ({ page }) => {
  const record = networkByPage.get(page);
  if (record) {
    await replacePrivate(`browser-network-${record.scenario}.json`, {
      requests: record.requests,
    });
    const info = test.info();
    const error = info.errors[0];
    const errorText = error?.message ?? "";
    const category = errorText.includes("strict mode violation") ? "ambiguous_locator"
      : /timeout|timed out/i.test(errorText) ? "timeout"
        : error ? "assertion_or_runtime" : "none";
    const stack = error?.stack ?? "";
    const lineMatch = /databricks-live\.spec\.ts:(\d+):\d+/.exec(stack);
    const line = lineMatch ? Number(lineMatch[1]) : null;
    await replacePrivate(`browser-outcome-${record.scenario}.json`, {
      title: info.title,
      status: info.status,
      expected: info.expectedStatus,
      error_count: info.errors.length,
      failure_category: category,
      failure_line: line,
    });
  }
});

async function writePrivate(name: string, value: object): Promise<void> {
  const path = join(rendezvous as string, name);
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, JSON.stringify(value), { mode: 0o600, flag: "wx" });
    await chmod(temporary, 0o600);
    await link(temporary, path);
  } finally {
    await unlink(temporary).catch(() => undefined);
  }
}

async function replacePrivate(name: string, value: object): Promise<void> {
  const path = join(rendezvous as string, name);
  const temporary = `${path}.${randomUUID()}.tmp`;
  try {
    await writeFile(temporary, JSON.stringify(value), { mode: 0o600, flag: "wx" });
    await chmod(temporary, 0o600);
    await rename(temporary, path);
  } finally {
    await unlink(temporary).catch(() => undefined);
  }
}

async function writeStage(scenario: string, value: string): Promise<void> {
  await replacePrivate(`browser-stage-${scenario}.json`, { value });
}

async function waitForGate(name: string): Promise<void> {
  const path = join(rendezvous as string, name);
  await expect.poll(async () => access(path).then(() => true).catch(() => false), {
    intervals: [20, 50, 100],
  }).toBe(true);
}

test("@covers US-068-AC1 @covers US-068-AC5 live browser, remote TUI and one approval winner", async ({ page }) => {
  const apiOrigin = new URL(process.env.TETHER_LIVE_APP_URL as string).origin;
  const approvalStatuses: number[] = [];
  page.on("response", (response) => {
    if (response.url().startsWith(`${apiOrigin}${apiPrefix}/sessions/`)
      && response.url().includes("/confirmations/")) {
      approvalStatuses.push(response.status());
    }
  });

  await page.goto("./");
  await writeStage("shared", "page_loaded");
  await expect(page.getByRole("button", { name: "+ New session" })).toBeVisible();
  await page.getByRole("button", { name: "+ New session" }).click();
  await writeStage("shared", "session_created");
  await page.getByRole("textbox", { name: "Message", exact: true }).fill(
    "Run the Tether qualification scenario: shared-session.",
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await writeStage("shared", "message_sent");
  await expect(page.getByRole("heading", { name: "Permission requested: record_qualification" }))
    .toBeVisible();

  const sessionId = new URL(page.url()).pathname.split("/sessions/")[1];
  expect(sessionId).toBeTruthy();
  const pendingResponse = await page.evaluate(async ({ prefix, id }) => {
    const response = await fetch(`${prefix}/sessions/${encodeURIComponent(id)}/pending`);
    return { status: response.status, value: response.ok ? await response.json() : null };
  }, { prefix: apiPrefix, id: sessionId });
  expect(pendingResponse.status).toBe(200);
  expect(pendingResponse.value?.tool).toBe("record_qualification");
  await writeStage("shared", "pending_loaded");
  await writePrivate("pending.json", {
    session_id: sessionId,
    invocation_id: pendingResponse.value.invocation_id,
    title: "Shared qualification",
  });
  await waitForGate("race-approval");
  await page.route(
    (url) => url.origin === appOrigin && url.pathname.includes("/confirmations/"),
    async (route) => {
      await writePrivate("browser-request-ready.json", { submitted: true });
      await waitForGate("release-browser-approval");
      await route.fallback();
    },
  );
  const approvalResponsePromise = page.waitForResponse((response) =>
    response.url().includes(`${apiPrefix}/sessions/${encodeURIComponent(sessionId)}/confirmations/`),
  );
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  const approvalResponse = await approvalResponsePromise;
  if (approvalResponse.status() === 202) {
    const accepted = await approvalResponse.json() as { turn_id?: unknown };
    expect(typeof accepted.turn_id).toBe("string");
    await writePrivate("browser-accepted.json", { turn_id: accepted.turn_id });
  }

  await expect(page.locator(".tether-message-assistant").getByText(
    "Fabricated Tether qualification effect completed.", { exact: true },
  ))
    .toBeVisible();
  expect(approvalStatuses).toHaveLength(1);
  expect([200, 202]).toContain(approvalStatuses[0]);
  await page.reload();
  await expect(page.locator(".tether-message-assistant").getByText(
    "Fabricated Tether qualification effect completed.", { exact: true },
  ))
    .toBeVisible();
  await expect(page.locator(".tether-message-tool")).toHaveCount(1);
  await writePrivate("browser-finished.json", { approval_status: approvalStatuses[0] });
});

test("@covers US-068-AC8 live bounded stop retains unknown effect barrier after restart", async ({ page }) => {
  await page.goto("./");
  await writeStage("unknown", "page_loaded");
  await page.getByRole("button", { name: "+ New session" }).click();
  await page.getByRole("textbox", { name: "Message", exact: true }).fill(
    "Run the Tether qualification scenario: unknown-effect.",
  );
  await page.getByRole("button", { name: "Send message" }).click();
  await writeStage("unknown", "message_sent");
  await expect(page.getByRole("heading", { name: "Permission requested: record_qualification" }))
    .toBeVisible();

  const sessionId = new URL(page.url()).pathname.split("/sessions/")[1];
  expect(sessionId).toBeTruthy();
  const pendingResponse = await page.evaluate(async ({ prefix, id }) => {
    const response = await fetch(`${prefix}/sessions/${encodeURIComponent(id)}/pending`);
    return { status: response.status, value: response.ok ? await response.json() : null };
  }, { prefix: apiPrefix, id: sessionId });
  expect(pendingResponse.status).toBe(200);
  expect(pendingResponse.value?.arguments).toEqual({ scenario: "unknown-effect" });
  await writePrivate("unknown-pending.json", {
    session_id: sessionId,
    invocation_id: pendingResponse.value.invocation_id,
  });

  await waitForGate("approve-unknown-effect");
  const approvalResponse = page.waitForResponse((response) =>
    response.url().includes(`${apiPrefix}/sessions/${encodeURIComponent(sessionId)}/confirmations/`),
  );
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  const accepted = await approvalResponse;
  expect(accepted.status()).toBe(202);
  const acceptedTurn = await accepted.json() as { turn_id: string };
  expect(typeof acceptedTurn.turn_id).toBe("string");
  const turnId = acceptedTurn.turn_id;
  await writePrivate("unknown-accepted.json", { turn_id: acceptedTurn.turn_id });
  await expect.poll(async () => page.evaluate(async ({ prefix, id }) => {
    const response = await fetch(`${prefix}/sessions/${encodeURIComponent(id)}/effects`);
    if (!response.ok) return false;
    const value = await response.json();
    return value.items?.some((item: { tool: string }) => item.tool === "record_qualification") ?? false;
  }, { prefix: apiPrefix, id: sessionId })).toBe(true);
  await writePrivate("effect-started.json", {
    session_id: sessionId,
    invocation_id: pendingResponse.value.invocation_id,
    turn_id: acceptedTurn.turn_id,
  });

  await waitForGate("restart-complete");
  await page.reload();
  const recovered = await page.evaluate(async ({ prefix, id, turn }) => {
    const [turnResponse, effectsResponse] = await Promise.all([
      fetch(`${prefix}/sessions/${encodeURIComponent(id)}/turns/${encodeURIComponent(turn)}`),
      fetch(`${prefix}/sessions/${encodeURIComponent(id)}/effects`),
    ]);
    return {
      turnStatus: turnResponse.status,
      turn: turnResponse.ok ? await turnResponse.json() : null,
      effectsStatus: effectsResponse.status,
      effects: effectsResponse.ok ? await effectsResponse.json() : null,
    };
  }, { prefix: apiPrefix, id: sessionId, turn: turnId });
  expect(recovered.turnStatus).toBe(200);
  expect(recovered.turn).toMatchObject({ turn_id: turnId });
  expect(["cancelled", "interrupted"]).toContain(recovered.turn.stop_reason);
  expect(recovered.effectsStatus).toBe(200);
  expect(recovered.effects.items).toHaveLength(1);
  expect(recovered.effects.items[0]).toMatchObject({
    invocation_id: pendingResponse.value.invocation_id,
    tool: "record_qualification",
    reason: "outcome_unknown",
  });

  const blocked = await page.evaluate(async ({ prefix, id }) => {
    const response = await fetch(`${prefix}/sessions/${encodeURIComponent(id)}/close`, {
      method: "POST",
    });
    const value = await response.json();
    return { status: response.status, code: value.code };
  }, { prefix: apiPrefix, id: sessionId });
  expect(blocked.status).toBe(403);
  expect(blocked.code).toBe("denied");
  await writePrivate("unknown-finished.json", { stop_reason: recovered.turn.stop_reason });
});
