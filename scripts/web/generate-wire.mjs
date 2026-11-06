import { readFile, writeFile, mkdir } from "node:fs/promises";
import { compile } from "json-schema-to-typescript";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import standaloneCode from "ajv/dist/standalone/index.js";

const source = new URL("../../packages/web-api/schema/http-v1.json", import.meta.url);
const directory = new URL("../../packages/client-ts/src/wire/", import.meta.url);
const registry = JSON.parse(await readFile(source, "utf8"));
const names = Object.keys(registry.$defs).sort();
const root = structuredClone(registry);
root.title = "TetherWire";
root.type = "object";
root.properties = Object.fromEntries(names.map((name) => [name, { $ref: `#/$defs/${name}` }]));
root.required = names;
root.additionalProperties = false;
const types = await compile(root, "TetherWire", {
  bannerComment: "/* Generated from the Python HTTP schema. Do not edit. */",
  unreachableDefinitions: true, unknownAny: true,
});
const counters = new Set();
function visit(value, key) {
  if (!value || typeof value !== "object") return;
  if (value["x-tether-counter"] && key) counters.add(key);
  if (value.properties) for (const [name, item] of Object.entries(value.properties)) visit(item, name);
  for (const branch of [...(value.anyOf ?? []), ...(value.oneOf ?? [])]) visit(branch, key);
  if (value.$defs) for (const item of Object.values(value.$defs)) visit(item);
}
visit(registry);
const counterText = `/* Generated counter names; arbitrary user JSON is opaque. */\nexport const COUNTER_NAMES = ${JSON.stringify([...counters].sort())} as const;\n`;
const ajv = new Ajv2020({ strict: false, allErrors: false, validateFormats: true, code: { esm: true, source: true } });
addFormats.default(ajv);
ajv.addFormat("tether-counter", /^-?(0|[1-9][0-9]*)$/);
ajv.addSchema(registry);
const validatorRefs = Object.fromEntries(names.map((name) => [`validate_${name}`, `${registry.$id}#/$defs/${name}`]));
let validatorModule = standaloneCode.default(ajv, validatorRefs);
validatorModule = validatorModule
  .replace(/const (func\d+) = require\("ajv\/dist\/runtime\/ucs2length"\)\.default;/g, "const $1 = ucs2length;")
  .replace(/require\("ajv-formats\/dist\/formats"\)\.fullFormats/g, "formatDefinitions.fullFormats");
if (validatorModule.includes("require(") || validatorModule.includes("new Function(") || validatorModule.includes("eval(")) {
  throw new Error("generated validators contain a dynamic or unresolved runtime loader");
}
validatorModule = `import ucs2lengthModule from "ajv/dist/runtime/ucs2length.js";\nimport * as formatDefinitions from "ajv-formats/dist/formats.js";\nconst ucs2length = typeof ucs2lengthModule === "function" ? ucs2lengthModule : ucs2lengthModule.default;\n${validatorModule}\nexport const validators = { ${names.map((name) => `${JSON.stringify(name)}: validate_${name}`).join(", ")} };\n`;
const validatorTypes = `import type { ValidateFunction } from "ajv";\n${names.map((name) => `export declare const validate_${name}: ValidateFunction;`).join("\n")}\nexport declare const validators: Record<string, ValidateFunction>;\n`;
await mkdir(directory, { recursive: true });
for (const [name, content] of [["generated.ts", types], ["schemas.json", JSON.stringify(registry, null, 2) + "\n"], ["counters.ts", counterText], ["validators.generated.js", validatorModule], ["validators.generated.d.ts", validatorTypes]]) {
  const destination = new URL(name, directory);
  if (process.argv.includes("--check")) {
    if (await readFile(destination, "utf8") !== content) throw new Error(`stale generated file: ${name}`);
  } else await writeFile(destination, content);
}
console.log(`Wire schema: ${names.length} definitions; ${counters.size} exact counter names`);
