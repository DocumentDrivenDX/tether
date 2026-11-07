import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import { mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { sanitizedSbom } from "./sbom.mjs";

const root = resolve(import.meta.dirname, "../..");
const scratch = await mkdtemp(join(tmpdir(), "tether-npm-consumer-"));
const packages = ["packages/client-ts", "packages/react", "packages/ui"];
const report = join(root, "artifacts/npm-package-report.json");
await mkdir(join(root, "artifacts"), { recursive: true });
await rm(report, { force: true });
function captured(program, args, options = {}) {
  try {
    return execFileSync(program, args, { maxBuffer: 2 * 1024 * 1024, ...options });
  } catch (error) {
    if (error.code === "ENOBUFS") {
      mkdirSync(join(root, "artifacts"), { recursive: true, mode: 0o700 });
      writeFileSync(
        join(root, "artifacts/packages-incomplete.json"),
        JSON.stringify({ schema_version: 1, status: "incomplete", framework: "packages", reason: "output_capture_failed", phase: "execution" }),
        { mode: 0o600 },
      );
      throw new Error("package_output_capture_exhausted");
    }
    throw error;
  }
}
// Consumer installs prefer the cache that `npm ci` filled for this checkout. That
// cache holds tarballs but not registry metadata for range resolution, so a fresh
// host may still need the registry; a fixed macOS-only path was absent on Linux.
const npmCache = process.env.npm_config_cache || captured("npm", ["config", "get", "cache"], { encoding: "utf8" }).trim();
const npm = (args, cwd) => captured("npm", ["--cache", npmCache, ...args], { cwd, encoding: "utf8" });
try {
  const archives = [];
  for (const [index, directory] of packages.entries()) {
    const copies = [];
    for (const pass of ["first", "second"]) {
      const destination = join(scratch, `${index}-${pass}`);
      await mkdir(destination);
      const [manifest] = JSON.parse(npm(["pack", "--json", "--pack-destination", destination], resolve(root, directory)));
      for (const file of ["LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "dist/index.js", "dist/index.d.ts"]) assert.ok(manifest.files.some((entry) => entry.path === file), `${directory}: missing ${file}`);
      // @covers US-069-AC3: inspect actual packed exports and framework peer declarations.
      const metadata = JSON.parse(captured("tar", ["-xOf", join(destination, manifest.filename), "package/package.json"], { encoding: "utf8" }));
      assert.equal(metadata.exports["."].types, "./dist/index.d.ts");
      assert.equal(metadata.exports["."].import, "./dist/index.js");
      if (metadata.name === "@tether/client") assert.deepEqual(metadata.peerDependencies, {});
      else assert.equal(metadata.peerDependencies.react, "19.3.1");
      if (metadata.name === "@tether/ui") {
        assert.equal(metadata.exports["./styles.css"], "./styles/tokens.css");
        assert.ok(manifest.files.some((entry) => entry.path === "styles/tokens.css"));
      }
      for (const file of ["LICENSE-MIT", "LICENSE-APACHE", "NOTICE"]) {
        assert.deepEqual(captured("tar", ["-xOf", join(destination, manifest.filename), `package/${file}`]), await readFile(join(root, file)), `${directory}: changed ${file}`);
      }
      copies.push(join(destination, manifest.filename));
    }
    const hashes = await Promise.all(copies.map(async (path) => createHash("sha256").update(await readFile(path)).digest("hex")));
    assert.equal(hashes[0], hashes[1], `${directory}: non-reproducible archive`);
    archives.push(copies[0]);
  }
  const clientOnly = join(scratch, "client-only");
  await mkdir(clientOnly);
  await writeFile(join(clientOnly, "package.json"), JSON.stringify({ name: "tether-client-consumer", version: "0.2.0-dev.0", private: true, type: "module" }));
  npm(["install", "--prefer-offline", "--ignore-scripts", "--no-audit", "--no-fund", archives[0]], clientOnly);
  const closure = JSON.parse(npm(["ls", "--all", "--json"], clientOnly));
  // @covers US-067-AC1: fresh installed closure and import have no React or ambient credential traffic.
  assert.ok(!JSON.stringify(closure).match(/"(react|react-dom|@tether\/react|@tether\/ui)"/), "framework-free client closure contains React");
  const source = `import assert from "node:assert/strict";
import { TetherClient } from "@tether/client";
let calls = 0;
const client = new TetherClient({ baseUrl: "https://fabricated.test/api/v1", fetch: async () => { calls++; return Response.json({methods:{send:"supported"},tiers:{live:"available",archive:"unavailable"}}); } });
assert.equal(calls, 0);
assert.equal((await client.capabilities()).methods.send, "supported");
assert.equal(calls, 1);`;
  await writeFile(join(clientOnly, "proof.mjs"), source);
  captured(process.execPath, ["proof.mjs"], { cwd: clientOnly, stdio: "pipe" });
  const combined = join(scratch, "combined");
  await mkdir(combined);
  await writeFile(join(combined, "package.json"), JSON.stringify({ name: "tether-react-consumer", version: "0.2.0-dev.0", private: true, type: "module" }));
  npm(["install", "--prefer-offline", "--ignore-scripts", "--no-audit", "--no-fund", ...archives, "react@19.3.1", "react-dom@19.3.1", "@types/react@19.3.0"], combined);
  const peers = JSON.parse(npm(["ls", "react", "--all", "--json"], combined));
  assert.equal(peers.dependencies.react.version, "19.3.1");
  const sbom = sanitizedSbom(JSON.parse(npm(["sbom", "--offline", "--sbom-format", "cyclonedx"], combined)));
  assert.ok(sbom.components.some((item) => item.name === "@tether/ui"));
  assert.ok(sbom.components.some((item) => item.name === "react"));
  assert.ok(!JSON.stringify(sbom).includes(scratch), "SBOM leaked a local artifact path");
  await writeFile(join(combined, "consumer.tsx"), `import { TetherClient } from "@tether/client";
import { ClientProvider } from "@tether/react";
import { SessionWorkspace, type SessionLayoutProps, type MessageRendererProps } from "@tether/ui";
import "@tether/ui/styles.css";
const client = new TetherClient({baseUrl:"https://fabricated.test/api/v1"});
function Layout({navigation,content}:SessionLayoutProps){return <section>{navigation}{content}</section>}
function Message({message}:MessageRendererProps){return <p>{message.content}</p>}
export const view=<ClientProvider client={client}><SessionWorkspace onSelect={()=>undefined} slots={{Layout,Message}} /></ClientProvider>;
`);
  // @covers US-069-AC8: compile the actual public guide snippets against installed artifacts.
  const snippets = [...(await readFile(join(root, "docs/react.md"), "utf8")).matchAll(/^```tsx\n([\s\S]*?)^```/gm)];
  assert.equal(snippets.length, 3, "actual React guide snippets missing");
  for (const [index, match] of snippets.entries()) {
    const declarations = "declare const auth: {currentAccessToken(): Promise<string | undefined>};\ndeclare const selectedId: string;\ndeclare function navigate(path: string): void;\n";
    await writeFile(join(combined, `guide-${index}.tsx`), declarations + match[1]);
  }
  await writeFile(join(combined, "tsconfig.json"), JSON.stringify({ compilerOptions: { strict: true, exactOptionalPropertyTypes: true, noUncheckedIndexedAccess: true, target: "ES2022", module: "NodeNext", moduleResolution: "NodeNext", jsx: "react-jsx", noEmit: true, types: [] }, include: ["consumer.tsx", "guide-*.tsx"] }));
  captured(process.execPath, [resolve(root, "node_modules/typescript/bin/tsc"), "--project", "tsconfig.json"], { cwd: combined, stdio: "pipe" });
  await writeFile(join(combined, "proof.mjs"), `import assert from "node:assert/strict";
import {renderToString} from "react-dom/server";
import {createElement} from "react";
import {DefaultMessage} from "@tether/ui";
const html=renderToString(createElement(DefaultMessage,{message:{role:"assistant",content:"<script>fabricated</script>"}}));
assert.ok(html.includes("&lt;script&gt;"));assert.ok(!html.includes("<script>"));`);
  captured(process.execPath, ["proof.mjs"], { cwd: combined, stdio: "pipe" });
  npm(["install", "--save-dev", "--prefer-offline", "--ignore-scripts", "--no-audit", "--no-fund", "jsdom@30.1.3"], combined);
  await writeFile(join(combined, "consumer-view.mjs"), await readFile(join(root, "scripts/web/consumer-view.mjs")));
  // @covers US-069-AC4: execute custom layout/message slots with the installed provider and client.
  // Cold JSDOM import alone measured 26 seconds; allow bounded cold loading,
  // while the consumer independently enforces five seconds after unmount.
  captured(process.execPath, ["consumer-view.mjs"], { cwd: combined, stdio: "pipe" });
  await writeFile(report, JSON.stringify({
    schema_version: 1,
    status: "passed",
    sbom,
    test_results: ["reproducible-archives", "framework-free-import", "typed-react-slots", "text-escaping"]
      .map((name) => ({ node_id: `scripts/web/check-packages.mjs::${name}`, outcome: "passed" })),
  }, null, 2) + "\n");
  console.log("Three reproducible npm archives, framework-free client and clean typed/React consumers pass");
} finally { await rm(scratch, { recursive: true, force: true }); }
