import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("@covers US-067-AC6 real prefixed UI, approved turn, committed history and keyboard accessibility", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const foreignRequests: string[] = [];
  page.on("request", (item) => { if (!new URL(item.url()).pathname.startsWith("/assistant/")) foreignRequests.push(item.url()); });
  await page.goto("/assistant/");
  await expect(page.getByRole("heading", { name: "Where will your next thought lead?" })).toBeVisible();
  await page.getByRole("button", { name: "+ New session" }).click();
  await expect(page.getByRole("heading", { name: "Start with a thought." })).toBeVisible();
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("<script>window.fixtureInjected=true</script> Review this fabricated label.");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page.getByRole("heading", { name: "Permission requested: record_demo" })).toBeVisible();
  await expect(page.getByText("waiting · Connected", { exact: true })).toBeVisible();
  await page.screenshot({ path: "docs/media/web-approval.png", fullPage: true });
  await page.getByRole("button", { name: "Approve", exact: true }).focus();
  await page.keyboard.press("Enter");
  const session = page.url().split("/sessions/")[1];
  expect(session).toBeTruthy();
  await expect.poll(async () => {
    const response = await request.get(`/assistant/api/v1/sessions/${session}/snapshot`);
    return (await response.json()).view.summary.status;
  }).toBe("idle");
  await expect(page.getByText("The fabricated record is complete.", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("article", { name: "Provisional reply" })).toHaveCount(0);
  await expect(page.locator(".tether-message-tool")).toHaveCount(1);
  await expect(page.locator(".tether-message-user")).toHaveCount(1);
  await expect(page.locator(".tether-message-user").first()).toContainText("Review this fabricated label.");
  expect(await page.evaluate(() => "fixtureInjected" in window)).toBe(false);
  const snapshot = await request.get(`/assistant/api/v1/sessions/${session}/snapshot`);
  expect(snapshot.ok()).toBe(true);
  const otherOwner = await request.get(`/assistant/api/v1/sessions/${session}/snapshot`, { headers: { "x-fixture-owner": "other" } });
  expect(otherOwner.status()).toBe(404);
  await page.reload();
  await expect(page.getByText("The fabricated record is complete.", { exact: true })).toBeVisible();
  await expect(page.getByText("<script>window.fixtureInjected=true</script> Review this fabricated label.", { exact: true })).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.screenshot({ path: "docs/media/web-conversation.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "docs/media/web-mobile.png", fullPage: true });
  const overflowingNodes = await page.evaluate(() => [...document.querySelectorAll("body *")]
    .map((node) => {
      const box = node.getBoundingClientRect();
      return { tag: node.tagName, className: node.getAttribute("class"), left: box.left, right: box.right, width: box.width };
    })
    .filter((box) => box.left < 0 || box.right > document.documentElement.clientWidth)
    .slice(0, 10));
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
    JSON.stringify(overflowingNodes),
  ).toBe(true);
  const mobileAccessibility = await new AxeBuilder({ page }).analyze();
  expect(mobileAccessibility.violations).toEqual([]);
  expect(errors).toEqual([]);
  expect(foreignRequests).toEqual([]);
});


test("real browser cancel settles the turn without advertising restart recovery", async ({ page, request }) => {
  await page.goto("/assistant/");
  await page.getByRole("button", { name: "+ New session" }).click();
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Hold this fabricated turn for cancellation.");
  await page.getByRole("button", { name: "Send message" }).click();
  await page.getByText("Session controls", { exact: true }).click();
  const sessionId = new URL(page.url()).pathname.split("/sessions/")[1];
  expect(sessionId).toBeTruthy();
  await expect.poll(async () => {
    const pending = await request.get(`/assistant/api/v1/sessions/${sessionId}/snapshot`);
    return (await pending.json()).view.summary.active_turn_id;
  }).toBeTruthy();
  const snapshot = await request.get(`/assistant/api/v1/sessions/${sessionId}/snapshot`);
  expect(snapshot.ok()).toBe(true);
  const activeTurn = (await snapshot.json()).view.summary.active_turn_id as string;
  expect(activeTurn).toBeTruthy();
  await page.getByRole("button", { name: "Cancel active turn" }).click();
  await expect(page.getByRole("button", { name: "Cancel active turn" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Resume session" })).toHaveCount(0);
  const cancelled = await request.get(`/assistant/api/v1/sessions/${sessionId}/turns/${activeTurn}`);
  expect(cancelled.ok()).toBe(true);
  expect((await cancelled.json()).stop_reason).toBe("cancelled");
  const settled = await request.get(`/assistant/api/v1/sessions/${sessionId}/snapshot`);
  const summary = (await settled.json()).view.summary;
  expect(summary.status).toBe("idle");
  expect(summary.resumable).toBe(false);
  await page.getByRole("button", { name: "Close session", exact: true }).click();
  await expect.poll(async () => {
    const closed = await request.get(`/assistant/api/v1/sessions/${sessionId}/snapshot`);
    return (await closed.json()).view.summary.status;
  }).toBe("closed");
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Restore session" })).toHaveCount(0);
  const history = await request.get(`/assistant/api/v1/sessions/${sessionId}/entries`);
  expect(history.ok()).toBe(true);
});


test("@covers US-066-AC6 real browser reads the terminal result recovered from a killed process", async ({ page, request }) => {
  await page.goto("/assistant/");
  const recoveredSession = page.getByRole("button", { name: /Interrupted browser recovery/ });
  await expect(recoveredSession).toBeVisible();
  await recoveredSession.click();
  await expect(page.getByRole("heading", { name: "Interrupted browser recovery" })).toBeVisible();
  const sessionId = new URL(page.url()).pathname.split("/sessions/")[1];
  expect(sessionId).toBeTruthy();
  const interrupted = await request.get(`/assistant/api/v1/sessions/${sessionId}/snapshot`);
  expect(interrupted.ok()).toBe(true);
  const before = (await interrupted.json()).view.summary;
  expect(before.status).toBe("interrupted");
  expect(before.resumable).toBe(true);
  const entryPage = await request.get(`/assistant/api/v1/sessions/${sessionId}/entries?limit=100`);
  expect(entryPage.ok()).toBe(true);
  const started = (await entryPage.json()).items.find(
    (entry: { kind: string; turn_id: string | null }) => entry.kind === "turn_started" && entry.turn_id,
  );
  expect(started).toBeTruthy();
  const recoveredResult = await request.get(
    `/assistant/api/v1/sessions/${sessionId}/turns/${started.turn_id}`,
  );
  expect(recoveredResult.ok()).toBe(true);
  expect(await recoveredResult.json()).toMatchObject({
    turn_id: started.turn_id,
    stop_reason: "interrupted",
  });
  await page.getByText("Session controls", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume session" })).toBeVisible();

  await page.getByRole("button", { name: "Resume session" }).click();
  await expect(page.getByRole("region", { name: "Conversation" }).getByText("The fabricated record is complete.", { exact: true })).toBeVisible();
  await expect.poll(async () => {
    const response = await request.get(`/assistant/api/v1/sessions/${sessionId}/snapshot`);
    return (await response.json()).view.summary.status;
  }).toBe("idle");
  const resumed = await request.get(`/assistant/api/v1/sessions/${sessionId}/snapshot`);
  expect(resumed.ok()).toBe(true);
  const after = (await resumed.json()).view.summary;
  expect(after.status).toBe("idle");
  expect(after.resumable).toBe(false);
  await expect(page.getByRole("region", { name: "Conversation" }).getByText("The fabricated record is complete.", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("article", { name: "Provisional reply" })).toHaveCount(0);
});
