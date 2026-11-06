import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { resolve, relative, dirname } from "node:path";
import ts from "typescript";

const root = resolve(import.meta.dirname, "../..");
const layers = ["packages/client-ts", "packages/react", "packages/ui", "apps/reference-web"];
const allowed = [[], ["@tether/client"], ["@tether/client", "@tether/react"], ["@tether/client", "@tether/react", "@tether/ui", "@tether/ui/styles.css"]];
async function walk(directory) {
  const files = [];
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const name = resolve(directory, item.name);
    if (item.isDirectory()) files.push(...await walk(name));
    else if (/\.[cm]?[jt]sx?$/.test(name)) files.push(name);
  }
  return files;
}
function violation(owner, specifier) {
  if (specifier.startsWith("@tether/") && !allowed[owner].includes(specifier)) return true;
  if (owner === 0 && /^(react|react-dom|@tanstack\/react-query)(\/|$)/.test(specifier)) return true;
  return specifier.includes("tether_web_") || specifier.includes("tether_databricks");
}
function imports(source, name) {
  const result = [];
  const file = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true);
  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) result.push(node.moduleSpecifier);
    if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || node.expression.getText(file) === "require")) result.push(node.arguments[0]);
    if (ts.isImportTypeNode(node) && ts.isLiteralTypeNode(node.argument)) result.push(node.argument.literal);
    ts.forEachChild(node, visit);
  }
  visit(file);
  return result.map((node) => node && ts.isStringLiteralLike(node) ? node.text : null);
}
function checkSource(owner, file, source) {
  const errors = [];
  for (const specifier of imports(source, file)) {
    if (specifier === null) { errors.push("computed module loading is not supported"); continue; }
    if (violation(owner, specifier)) errors.push(`forbidden import ${specifier}`);
    if (specifier.startsWith(".")) {
      const target = resolve(dirname(file), specifier);
      if (!target.startsWith(resolve(root, layers[owner], "src") + "/")) errors.push("private cross-package path");
    }
  }
  return errors;
}
// Plant source, then exercise the same AST and path checks used on actual files.
const client = resolve(root, layers[0], "src/planted.ts");
for (const source of [
  'import React from "react";', 'export * from "@tether/ui";',
  'const x = import("@tether/react");', 'type X = import("react").ReactNode;',
  'const x = require("@tether/client/private");', 'import "../../ui/src/index.js";',
  'const x = import(variable);',
]) assert.ok(checkSource(0, client, source).length, `negative control escaped: ${source}`);
assert.deepEqual(checkSource(0, client, 'const text = "import React from react"; // import "react"\nimport "./types.js";'), []);
const errors = [];
for (const [owner, layer] of layers.entries()) {
  for (const file of await walk(resolve(root, layer, "src"))) {
    errors.push(...checkSource(owner, file, await readFile(file, "utf8")).map((error) => `${relative(root, file)}: ${error}`));
  }
  const manifest = JSON.parse(await readFile(resolve(root, layer, "package.json"), "utf8"));
  for (const dependency of Object.keys({ ...manifest.dependencies, ...manifest.peerDependencies })) {
    if (violation(owner, dependency)) errors.push(`${layer}: forbidden declared dependency ${dependency}`);
  }
}
if (errors.length) { console.error(errors.join("\n")); process.exitCode = 1; }
else console.log("Browser package AST/dependency boundaries and planted negative controls pass");
