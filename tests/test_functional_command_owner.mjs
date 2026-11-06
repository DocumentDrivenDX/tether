import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const OWNER = fileURLToPath(new URL("../scripts/web/run-functional-command.mjs", import.meta.url));

async function waitForFile(path, child, label, { allowOwnerExit = false } = {}) {
  while (true) {
    if (!allowOwnerExit && (child.exitCode !== null || child.signalCode !== null)) {
      throw new Error(`${label}_owner_exited_early_${child.exitCode}`);
    }
    if (
      allowOwnerExit &&
      (child.exitCode !== null || child.signalCode !== null) &&
      !groupExists(child.pid)
    ) {
      throw new Error(`${label}_owned_group_disappeared_before_signal`);
    }
    try {
      return await readFile(path, "utf8");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      await delay(10);
    }
  }
}

async function waitForExit(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return child.exitCode ?? (child.signalCode === "SIGINT" ? 130 : 143);
  }
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve(code ?? (signal === "SIGINT" ? 130 : 143)));
  });
}

function groupExists(pgid) {
  try {
    process.kill(-pgid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") return false;
    if (error.code === "EPERM") return true;
    throw error;
  }
}

async function waitForGroupAbsent(pgid) {
  while (groupExists(pgid)) await delay(10);
}

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "tether-functional-owner-"));
  const command = join(root, "node_modules", "vitest");
  await mkdir(command, { recursive: true });
  const ready = join(root, "ready.json");
  const spawned = join(root, "spawned.json");
  const descendantReady = join(root, "descendant-ready.json");
  const signal = join(root, "signal.json");
  const descendantSignal = join(root, "descendant-signal.json");
  const release = join(root, "release");
  const leaderExit = join(root, "leader-exit");
  const emitOutput = join(root, "emit-output");
  const descendantCode = `
    import { existsSync, writeFileSync } from "node:fs";
    import { setTimeout } from "node:timers/promises";
    if (process.env.TETHER_FAKE_DESCENDANT_FAIL === "1") process.exit(4);
    const signal = ${JSON.stringify(descendantSignal)};
    const ready = ${JSON.stringify(descendantReady)};
    const release = ${JSON.stringify(release)};
    process.on("SIGTERM", () => writeFileSync(signal, "term"));
    writeFileSync(ready, "handler installed");
    while (!existsSync(release)) await setTimeout(10);
  `;
  const fake = `
    import { spawn } from "node:child_process";
    import { existsSync, writeFileSync } from "node:fs";
    import { setTimeout } from "node:timers/promises";
    const ready = ${JSON.stringify(ready)};
    const spawnedPath = ${JSON.stringify(spawned)};
    const signal = ${JSON.stringify(signal)};
    const release = ${JSON.stringify(release)};
    const descendantSignal = ${JSON.stringify(descendantSignal)};
    const emitOutput = ${JSON.stringify(emitOutput)};
    const descendantReady = ${JSON.stringify(descendantReady)};
    const descendantCode = ${JSON.stringify(descendantCode)};
    const descendant = spawn(process.execPath, ["--input-type=module", "-e", descendantCode], { stdio: "ignore" });
    let descendantExited = false;
    descendant.once("exit", () => { descendantExited = true; });
    process.on("SIGTERM", () => writeFileSync(signal, "term"));
    writeFileSync(spawnedPath, JSON.stringify({ pid: process.pid, descendant: descendant.pid }));
    while (!existsSync(descendantReady)) {
      if (descendantExited) process.exit(3);
      await setTimeout(10);
    }
    writeFileSync(ready, JSON.stringify({ pid: process.pid, descendant: descendant.pid }));
    if (process.env.TETHER_FAKE_OUTPUT_BARRIER === "1") {
      while (!existsSync(emitOutput)) await setTimeout(10);
      process.stdout.write("fabricated output after consumer close\\n");
      process.stderr.write("fabricated diagnostic after consumer close\\n");
    }
    if (process.env.TETHER_FAKE_LEADER_EXIT === "1") {
      const leaderExit = ${JSON.stringify(leaderExit)};
      while (!existsSync(leaderExit)) await setTimeout(10);
      process.exit(0);
    }
    while (!existsSync(release)) await setTimeout(10);
    while (!existsSync(descendantSignal)) await setTimeout(10);
  `;
  await writeFile(join(command, "vitest.mjs"), fake);
  const packageCommand = join(root, "scripts", "web");
  await mkdir(packageCommand, { recursive: true });
  await writeFile(join(packageCommand, "check-packages.mjs"), fake);
  const packageReport = join(root, "artifacts", "npm-package-report.json");
  return { root, ready, spawned, signal, descendantSignal, release, leaderExit, emitOutput, packageReport };
}

async function childProcessGroup(paths) {
  try {
    return JSON.parse(await readFile(paths.spawned, "utf8")).pid;
  } catch (error) {
    if (error.code === "ENOENT") return undefined;
    throw error;
  }
}

async function releaseFixture(paths) {
  await Promise.all(
    [paths.release, paths.descendantSignal, paths.leaderExit].map((path) =>
      writeFile(path, "release").catch(() => undefined),
    ),
  );
}

async function settleOwnedFixture(paths, owner, processGroup) {
  await releaseFixture(paths);
  const exactGroup = processGroup ?? (await childProcessGroup(paths));
  if (owner && owner.exitCode === null && owner.signalCode === null) {
    owner.kill("SIGTERM");
    await waitForExit(owner);
  }
  if (exactGroup && groupExists(exactGroup)) {
    process.kill(-exactGroup, "SIGKILL");
    await waitForGroupAbsent(exactGroup);
  }
}

test("direct functional owner retains and cleans its exact child group across repeated signals", async () => {
  const paths = await fixture();
  let owner;
  let childPgid;
  try {
    owner = spawn(process.execPath, [OWNER, "vitest"], {
      cwd: paths.root,
      env: {
        ...process.env,
        TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED: "0",
        TETHER_FUNCTIONAL_WATCHDOG_MS: "1800000",
      },
      detached: true,
      stdio: "ignore",
    });
    const ready = JSON.parse(await waitForFile(paths.ready, owner, "direct"));
    childPgid = ready.pid;
    assert.ok(groupExists(childPgid));

    process.kill(owner.pid, "SIGTERM");
    await waitForFile(paths.signal, owner, "direct_signal");
    await waitForFile(paths.descendantSignal, owner, "descendant_signal");
    process.kill(owner.pid, "SIGTERM");
    await writeFile(paths.release, "release");

    assert.equal(await waitForExit(owner), 143);
    await waitForGroupAbsent(childPgid);
    assert.equal(groupExists(childPgid), false);
  } finally {
    await settleOwnedFixture(paths, owner, childPgid);
    await rm(paths.root, { recursive: true, force: true });
  }
});

test("direct owner escalates when a pipe-closed descendant ignores termination", async () => {
  const paths = await fixture();
  await mkdir(join(paths.root, "artifacts"), { recursive: true });
  await writeFile(paths.packageReport, "stale report");
  let owner;
  let childPgid;
  try {
    owner = spawn(process.execPath, [OWNER, "packages"], {
      cwd: paths.root,
      env: {
        ...process.env,
        TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED: "0",
        TETHER_FAKE_LEADER_EXIT: "1",
        TETHER_FUNCTIONAL_WATCHDOG_MS: "1800000",
      },
      detached: true,
      stdio: "ignore",
    });
    const ready = JSON.parse(await waitForFile(paths.ready, owner, "leader_exit"));
    childPgid = ready.pid;
    assert.ok(groupExists(childPgid));
    await writeFile(paths.leaderExit, "exit leader");
    await waitForFile(paths.descendantSignal, owner, "ignored_descendant_signal");
    assert.equal(await waitForExit(owner), 2);
    await assert.rejects(readFile(paths.packageReport, "utf8"), { code: "ENOENT" });
    await waitForGroupAbsent(childPgid);
    assert.equal(groupExists(childPgid), false);
  } finally {
    await settleOwnedFixture(paths, owner, childPgid);
    await rm(paths.root, { recursive: true, force: true });
  }
});

test("externally supervised wrapper inherits the caller group and leaves cleanup to that owner", async () => {
  const paths = await fixture();
  let owner;
  let ownerPgid;
  try {
    owner = spawn(process.execPath, [OWNER, "vitest"], {
      cwd: paths.root,
      env: {
        ...process.env,
        TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED: "1",
        TETHER_FUNCTIONAL_WATCHDOG_MS: "1800000",
      },
      detached: true,
      stdio: "ignore",
    });
    ownerPgid = owner.pid;
    const ready = JSON.parse(await waitForFile(paths.ready, owner, "external"));
    assert.notEqual(ready.pid, ownerPgid);
    owner.kill("SIGTERM");
    assert.equal(await waitForExit(owner), 143);
    assert.ok(groupExists(ownerPgid), "external child remains in the caller-owned group");
    process.kill(ready.pid, "SIGTERM");
    process.kill(ready.descendant, "SIGTERM");
    await waitForFile(paths.signal, owner, "external_signal", { allowOwnerExit: true });
    await waitForFile(paths.descendantSignal, owner, "external_descendant_signal", {
      allowOwnerExit: true,
    });
    await writeFile(paths.descendantSignal, "not-applicable");
    await writeFile(paths.release, "release");
    await waitForGroupAbsent(ownerPgid);
    assert.equal(groupExists(ownerPgid), false);
  } finally {
    await settleOwnedFixture(paths, owner, ownerPgid);
    await rm(paths.root, { recursive: true, force: true });
  }
});

test("owner reports a child failure before global readiness and leaves no child group", async () => {
  const paths = await fixture();
  let owner;
  let childPgid;
  try {
    owner = spawn(process.execPath, [OWNER, "vitest"], {
      cwd: paths.root,
      env: {
        ...process.env,
        TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED: "0",
        TETHER_FAKE_DESCENDANT_FAIL: "1",
        TETHER_FUNCTIONAL_WATCHDOG_MS: "1800000",
      },
      detached: true,
      stdio: "ignore",
    });
    const spawned = JSON.parse(await waitForFile(paths.spawned, owner, "early_failure"));
    childPgid = spawned.pid;
    assert.notEqual(childPgid, owner.pid);
    assert.equal(await waitForExit(owner), 3);
    await assert.rejects(readFile(paths.ready, "utf8"), { code: "ENOENT" });
    assert.equal(groupExists(childPgid), false);
  } finally {
    await settleOwnedFixture(paths, owner, childPgid);
    await rm(paths.root, { recursive: true, force: true });
  }
});

test("standalone mode refuses unsupported process-group ownership before framework spawn", async () => {
  const paths = await fixture();
  let owner;
  try {
    const bootstrap = `
      Object.defineProperty(process, "platform", { value: "win32" });
      process.argv = [process.argv[0], ${JSON.stringify(OWNER)}, "vitest"];
      await import(${JSON.stringify(pathToFileURL(OWNER).href)});
    `;
    owner = spawn(process.execPath, ["--input-type=module", "-e", bootstrap], {
      cwd: paths.root,
      env: {
        ...process.env,
        TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED: "0",
        TETHER_FUNCTIONAL_WATCHDOG_MS: "1000",
      },
      stdio: "ignore",
    });
    assert.equal(await waitForExit(owner), 2);
    assert.equal(await childProcessGroup(paths), undefined);
    await assert.rejects(readFile(paths.spawned, "utf8"), { code: "ENOENT" });
    assert.deepEqual(JSON.parse(await readFile(join(paths.root, "artifacts/vitest-incomplete.json"), "utf8")), {
      schema_version: 1,
      status: "incomplete",
      framework: "vitest",
      reason: "unsupported_platform",
      phase: "spawn",
    });
  } finally {
    await settleOwnedFixture(paths, owner, undefined);
    await rm(paths.root, { recursive: true, force: true });
  }
});

test("direct owner drains child output after its output consumer closes", async () => {
  const paths = await fixture();
  let owner;
  let childPgid;
  try {
    owner = spawn(process.execPath, [OWNER, "vitest"], {
      cwd: paths.root,
      env: {
        ...process.env,
        TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED: "0",
        TETHER_FAKE_OUTPUT_BARRIER: "1",
        TETHER_FUNCTIONAL_WATCHDOG_MS: "1800000",
      },
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    owner.stdout.on("error", () => undefined);
    owner.stderr.on("error", () => undefined);
    const ready = JSON.parse(await waitForFile(paths.ready, owner, "output_close"));
    childPgid = ready.pid;
    owner.stdout.destroy();
    owner.stderr.destroy();
    await writeFile(paths.emitOutput, "write after consumer close");
    await waitForFile(paths.signal, owner, "output_failure_signal");
    await waitForFile(paths.descendantSignal, owner, "output_failure_descendant_signal");
    await writeFile(paths.release, "release");
    assert.equal(await waitForExit(owner), 2);
    const marker = JSON.parse(
      await readFile(join(paths.root, "artifacts/vitest-incomplete.json"), "utf8"),
    );
    assert.deepEqual(marker, {
      schema_version: 1,
      status: "incomplete",
      framework: "vitest",
      reason: "output_capture_failed",
      phase: "execution",
    });
    await waitForGroupAbsent(childPgid);
    assert.equal(groupExists(childPgid), false);
  } finally {
    await settleOwnedFixture(paths, owner, childPgid);
    await rm(paths.root, { recursive: true, force: true });
  }
});
