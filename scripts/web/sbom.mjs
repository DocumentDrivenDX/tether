import assert from "node:assert/strict";

// Preserve installed identities, dependency edges and SPDX expressions. Never copy
// local archive locations, environment metadata, external references or paths.
export function sanitizedSbom(source) {
  assert.equal(source.bomFormat, "CycloneDX");
  const aliases = new Map();
  const root = "urn:tether:installed-npm-consumer";
  if (source.metadata?.component?.["bom-ref"]) aliases.set(source.metadata.component["bom-ref"], root);
  const components = new Map();
  for (const item of source.components ?? []) {
    const name = item.group ? `${item.group}/${item.name}` : item.name;
    assert.match(name, /^(?:@[a-z0-9._-]+\/)?[a-z0-9._-]+$/i);
    assert.match(item.version, /^[a-z0-9._+-]+$/i);
    const reference = `pkg:npm/${name.split("/").map(encodeURIComponent).join("/")}@${encodeURIComponent(item.version)}`;
    aliases.set(item["bom-ref"], reference);
    const licenses = (item.licenses ?? []).flatMap((entry) => {
      const expression = entry.expression ?? entry.license?.id;
      return typeof expression === "string" && /^[a-z0-9.+() -]+$/i.test(expression)
        ? [{ expression }] : [];
    });
    components.set(reference, {
      type: "library", "bom-ref": reference, name, version: item.version,
      purl: reference, licenses,
      properties: [{ name: "tether:license-metadata", value: licenses.length ? "observed-spdx" : "unavailable" }],
    });
  }
  const edges = new Map();
  for (const item of source.dependencies ?? []) {
    const reference = aliases.get(item.ref);
    assert.ok(reference, "unknown installed dependency source");
    const dependencies = (item.dependsOn ?? []).map((value) => {
      const target = aliases.get(value);
      assert.ok(target, "unknown installed dependency target");
      return target;
    });
    edges.set(reference, [...new Set([...(edges.get(reference) ?? []), ...dependencies])].sort());
  }
  assert.ok(components.size, "empty installed SBOM");
  return {
    bomFormat: "CycloneDX", specVersion: "1.6", version: 1,
    metadata: {
      component: { type: "application", "bom-ref": root, name: "tether-installed-npm-consumer", version: "0.2.0-dev.0" },
      properties: [{ name: "tether:scope", value: "Actual clean combined installed npm consumer; includes its declared React and typing dependencies" }],
    },
    components: [...components.values()].sort((left, right) => left.purl.localeCompare(right.purl)),
    dependencies: [...edges].map(([ref, dependsOn]) => ({ ref, dependsOn })).sort((left, right) => left.ref.localeCompare(right.ref)),
  };
}
