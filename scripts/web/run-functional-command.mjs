import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const DEFAULT_WATCHDOG_MS = 30 * 60 * 1000;
const MAX_WATCHDOG_MS = 2_147_483_647;
const CANONICAL_DECIMAL = /^[1-9][0-9]*$/;
const EXTERNALLY_SUPERVISED = process.env.TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED === "1";
const CLEANUP_GRACE_MS = 5_000;
const CLEANUP_BOUND_MS = 30_000;
const ignoreBrokenConsole = () => undefined;

process.stdout.on("error", ignoreBrokenConsole);
process.stderr.on("error", ignoreBrokenConsole);

const [framework, ...filters] = process.argv.slice(2);
const definitions = {
  vitest: {
    marker: "artifacts/vitest-incomplete.json",
    command: "node_modules/vitest/vitest.mjs",
    args: ["run", ...filters],
  },
  playwright: {
    marker: "artifacts/playwright-incomplete.json",
    command: "node_modules/playwright/cli.js",
    args: ["test", "--config", "tests/ui/playwright.config.ts", ...filters],
  },
  packages: {
    marker: "artifacts/packages-incomplete.json",
    report: "artifacts/npm-package-report.json",
    command: "scripts/web/check-packages.mjs",
    args: [],
  },
};

function watchdogMilliseconds() {
  const raw = process.env.TETHER_FUNCTIONAL_WATCHDOG_MS;
  if (raw === undefined) return DEFAULT_WATCHDOG_MS;
  if (!CANONICAL_DECIMAL.test(raw)) throw new Error("functional_watchdog_invalid");
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 1_000 || value > MAX_WATCHDOG_MS) {
    throw new Error("functional_watchdog_invalid");
  }
  return value;
}

function markerValue(path) {
  if (!existsSync(path)) return undefined;
  let value;
  try {
    value = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
  const validReasons = {
    owner_watchdog: "execution",
    cleanup_unconfirmed: "cleanup",
    unsupported_platform: "spawn",
    playwright_timedout: "execution",
    dispatch_failed: "spawn",
    owned_group_not_empty: "cleanup",
    output_capture_failed: "execution",
  };
  if (
    !value ||
    Object.keys(value).sort().join(",") !== "framework,phase,reason,schema_version,status" ||
    value.schema_version !== 1 ||
    value.status !== "incomplete" ||
    value.framework !== framework ||
    validReasons[value.reason] !== value.phase ||
    (value.reason === "playwright_timedout" && framework !== "playwright") ||
    (framework === "packages" && value.reason === "playwright_timedout")
  ) {
    return null;
  }
  return value;
}

function writeMarker(path, reason, phase) {
  mkdirSync(resolve("artifacts"), { recursive: true, mode: 0o700 });
  writeFileSync(
    path,
    JSON.stringify({ schema_version: 1, status: "incomplete", framework, reason, phase }),
    { mode: 0o600 },
  );
}

function standaloneUnsupportedPlatform() {
  return !EXTERNALLY_SUPERVISED && process.platform === "win32";
}

function forward(source, destination, onFailure) {
  return new Promise((resolvePromise) => {
    let failed = false;
    let ended = false;
    let settled = false;
    let pendingWrites = 0;
    const finish = () => {
      if (settled || !ended || (!failed && pendingWrites > 0)) return;
      settled = true;
      destination.off("error", destinationFailed);
      destination.off("close", destinationClosed);
      resolvePromise(failed);
    };
    const destinationFailed = () => {
      failed = true;
      onFailure();
      source.resume();
      finish();
    };
    const destinationClosed = () => {
      failed = true;
      onFailure();
      source.resume();
      finish();
    };
    const resumeSource = () => source.resume();
    destination.on("error", destinationFailed);
    destination.on("close", destinationClosed);
    source.on("data", (chunk) => {
      if (failed) return;
      pendingWrites += 1;
      let accepted;
      try {
        accepted = destination.write(chunk, (error) => {
          pendingWrites -= 1;
          if (error) destinationFailed();
          finish();
        });
      } catch {
        pendingWrites -= 1;
        destinationFailed();
        return;
      }
      if (!accepted && !failed) {
        source.pause();
        destination.once("drain", resumeSource);
      }
    });
    source.once("end", () => {
      ended = true;
      finish();
    });
    source.once("error", () => {
      failed = true;
      onFailure();
      ended = true;
      source.resume();
      finish();
    });
    source.once("close", () => {
      if (!ended) {
        failed = true;
        onFailure();
        ended = true;
      }
      finish();
    });
  });
}

function waitAtMost(promise, milliseconds) {
  let timer;
  return Promise.race([
    promise.then(() => true),
    new Promise((resolvePromise) => {
      timer = setTimeout(() => resolvePromise(false), milliseconds);
    }),
  ]).finally(() => clearTimeout(timer));
}

function processGroupExists(pid) {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") return false;
    if (error.code === "EPERM") return true;
    throw error;
  }
}

function signalOwnedGroup(pid, signal) {
  try {
    process.kill(-pid, signal);
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

async function waitForGroupAbsent(pid, milliseconds) {
  const deadline = Date.now() + milliseconds;
  while (processGroupExists(pid)) {
    if (Date.now() >= deadline) return false;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 50));
  }
  return true;
}

async function run() {
  const selected = definitions[framework];
  if (!selected) {
    process.stderr.write("functional_framework_invalid\n");
    process.exitCode = 1;
    return;
  }
  const watchdogMs = watchdogMilliseconds();
  const marker = resolve(selected.marker);
  mkdirSync(resolve("artifacts"), { recursive: true, mode: 0o700 });
  try {
    unlinkSync(marker);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (selected.report) {
    try {
      unlinkSync(resolve(selected.report));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  if (standaloneUnsupportedPlatform()) {
    writeMarker(marker, "unsupported_platform", "spawn");
    process.stderr.write("functional_owner_platform_unsupported: incomplete\n");
    process.exitCode = 2;
    return;
  }

  let child;
  let childPid;
  let closeResult;
  let spawnError;
  let requestedSignal;
  let cleanupPromise;
  let resolveSignal;
  const signalReceived = new Promise((resolvePromise) => {
    resolveSignal = resolvePromise;
  });
  let resolveWatchdog;
  const watchdogExpired = new Promise((resolvePromise) => {
    resolveWatchdog = resolvePromise;
  });
  let resolveOutputFailure;
  const outputFailure = new Promise((resolvePromise) => {
    resolveOutputFailure = resolvePromise;
  });
  const noteOutputFailure = () => resolveOutputFailure({ kind: "output_failure" });
  const watchdog = EXTERNALLY_SUPERVISED
    ? undefined
    : setTimeout(() => {
        resolveWatchdog("watchdog");
      }, watchdogMs);

  if (!EXTERNALLY_SUPERVISED) {
    for (const signal of ["SIGINT", "SIGTERM"]) {
      process.on(signal, () => {
        requestedSignal ??= signal;
        resolveSignal(signal);
      });
    }
  }

  const spawned = new Promise((resolvePromise) => {
    child = spawn(process.execPath, [resolve(selected.command), ...selected.args], {
      detached: !EXTERNALLY_SUPERVISED && process.platform !== "win32",
      stdio: ["inherit", "pipe", "pipe"],
    });
    child.once("spawn", () => {
      childPid = child.pid;
      resolvePromise(true);
    });
    child.once("error", (error) => {
      spawnError = error;
      resolvePromise(false);
    });
  });
  const closed = new Promise((resolvePromise) => {
    child.once("close", (code, signal) => {
      closeResult = { code, signal };
      resolvePromise(closeResult);
    });
  });
  const streams = Promise.all([
    forward(child.stdout, process.stdout, noteOutputFailure),
    forward(child.stderr, process.stderr, noteOutputFailure),
  ]);
  const complete = Promise.all([closed, streams]).then(
    ([result, captureFailures]) => ({
      kind: captureFailures.some(Boolean) ? "output_failure" : "complete",
      result,
    }),
    () => ({ kind: "output_failure" }),
  );

  const spawnOutcome = await Promise.race([
    spawned.then((started) => (started ? "spawned" : "spawn_failed")),
    watchdogExpired,
    ...(EXTERNALLY_SUPERVISED ? [] : [signalReceived]),
  ]);
  if (spawnOutcome === "spawn_failed") {
    await complete;
    writeMarker(marker, "dispatch_failed", "spawn");
    process.exitCode = 2;
  } else if (spawnOutcome === "watchdog" || spawnOutcome === "SIGINT" || spawnOutcome === "SIGTERM") {
    const cleanupConfirmed = await terminateOwnedChild(spawned, closed);
    if (spawnOutcome === "watchdog") {
      writeMarker(
        marker,
        cleanupConfirmed ? "owner_watchdog" : "cleanup_unconfirmed",
        cleanupConfirmed ? "execution" : "cleanup",
      );
      process.stderr.write(`${framework}_watchdog_exhausted: incomplete\n`);
      process.exitCode = 2;
    } else if (cleanupConfirmed) {
      process.exitCode = spawnOutcome === "SIGINT" ? 130 : 143;
    } else {
      writeMarker(marker, "cleanup_unconfirmed", "cleanup");
      process.exitCode = 2;
    }
  } else {
    const outcome = await Promise.race([
      complete,
      outputFailure,
      watchdogExpired,
      ...(EXTERNALLY_SUPERVISED ? [] : [signalReceived]),
    ]);
    if (outcome === "watchdog" || outcome === "SIGINT" || outcome === "SIGTERM") {
      const cleanupConfirmed = await terminateOwnedChild(spawned, closed);
      if (outcome === "watchdog") {
        writeMarker(
          marker,
          cleanupConfirmed ? "owner_watchdog" : "cleanup_unconfirmed",
          cleanupConfirmed ? "execution" : "cleanup",
        );
        process.stderr.write(`${framework}_watchdog_exhausted: incomplete\n`);
        process.exitCode = 2;
      } else if (cleanupConfirmed) {
        process.exitCode = outcome === "SIGINT" ? 130 : 143;
      } else {
        writeMarker(marker, "cleanup_unconfirmed", "cleanup");
        process.exitCode = 2;
      }
    } else if (outcome.kind === "output_failure") {
      const cleanupConfirmed = EXTERNALLY_SUPERVISED
        ? false
        : await terminateOwnedChild(spawned, closed);
      writeMarker(
        marker,
        cleanupConfirmed ? "output_capture_failed" : "cleanup_unconfirmed",
        cleanupConfirmed ? "execution" : "cleanup",
      );
      process.stderr.write(`${framework}_output_capture_failed: incomplete\n`);
      process.exitCode = 2;
    } else {
      const markerReceipt = markerValue(marker);
      if (markerReceipt) {
        process.stderr.write(`${framework}_${markerReceipt.reason}: incomplete\n`);
        process.exitCode = 2;
      } else if (existsSync(marker)) {
        process.stderr.write("framework_incomplete_marker_invalid\n");
        process.exitCode = 1;
      } else {
        process.exitCode = closeResult?.code ?? 1;
      }
      if (!EXTERNALLY_SUPERVISED && process.platform !== "win32" && childPid) {
        if (processGroupExists(childPid)) {
          const cleanupConfirmed = await terminateOwnedChild(spawned, closed);
          if (!cleanupConfirmed) {
            writeMarker(marker, "cleanup_unconfirmed", "cleanup");
            process.exitCode = 2;
          } else if (process.exitCode === 0) {
            writeMarker(marker, "owned_group_not_empty", "cleanup");
            process.exitCode = 2;
          }
        }
      }
    }
  }
  clearTimeout(watchdog);
  if (selected.report && process.exitCode !== 0) {
    try {
      unlinkSync(resolve(selected.report));
    } catch {
      writeMarker(marker, "cleanup_unconfirmed", "cleanup");
      process.exitCode = 2;
    }
  }
  if (spawnError) process.stderr.write("functional_child_dispatch_failed\n");

  async function terminateOwnedChild(spawnTask, leaderClosed) {
    if (cleanupPromise) return cleanupPromise;
    cleanupPromise = (async () => {
      if (EXTERNALLY_SUPERVISED) return false;
      if (!childPid) {
        const started = await spawnTask;
        if (!started || !childPid) return true;
      }
      try {
        signalOwnedGroup(childPid, "SIGTERM");
      } catch {
        return false;
      }
      if (!(await waitForGroupAbsent(childPid, CLEANUP_GRACE_MS))) {
        try {
          signalOwnedGroup(childPid, "SIGKILL");
        } catch {
          return false;
        }
      }
      if (!(await waitAtMost(leaderClosed, CLEANUP_BOUND_MS))) return false;
      try {
        return await waitForGroupAbsent(childPid, CLEANUP_BOUND_MS);
      } catch {
        return false;
      }
    })();
    return cleanupPromise;
  }
}

run().catch(() => {
  process.stderr.write("functional_command_owner_failed\n");
  process.exitCode = 2;
});
