import { readdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { gzipSync } from "node:zlib";
import { resolve, relative } from "node:path";
const root = resolve(import.meta.dirname, "../../packages/web-ui/src/tether_web_ui/assets");
const inventory = {};
let javascript = 0;
async function walk(directory) {
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, item.name);
    if (item.isDirectory()) await walk(path);
    else if (item.name !== "tether-assets.json") {
      const bytes = await readFile(path);
      inventory[relative(root, path)] = createHash("sha256").update(bytes).digest("hex");
      if (item.name.endsWith(".js")) javascript += gzipSync(bytes).length;
    }
  }
}
await walk(root);
if (!inventory["index.html"]) throw new Error("Missing browser entry");
await writeFile(resolve(root, "tether-assets.json"), JSON.stringify(inventory, null, 2) + "\n");
console.log(`Browser assets: ${Object.keys(inventory).length}; JS gzip total ${javascript} bytes`);
if (javascript > 250 * 1024) throw new Error("Reference JavaScript exceeds proposed 250 KiB gzip budget");
