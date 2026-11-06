import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useNavigate, useParams, useSearchParams, Routes, Route } from "react-router-dom";
import { TetherClient } from "@tether/client";
import { ClientProvider } from "@tether/react";
import { SessionWorkspace } from "@tether/ui";
import "@tether/ui/styles.css";
import "./reset.css";

interface PublicConfig { base_path: string; api_discovery_path: string }
function config(value: unknown): PublicConfig {
  if (typeof value !== "object" || value === null || !("base_path" in value) || !("api_discovery_path" in value)) throw new Error("Invalid UI configuration");
  const { base_path: base, api_discovery_path: discovery } = value;
  if (typeof base !== "string" || typeof discovery !== "string" || !discovery.startsWith("/") || discovery.startsWith("//") || !discovery.endsWith("/capabilities")) throw new Error("Invalid API discovery path");
  return { base_path: base, api_discovery_path: discovery };
}
function Workspace() {
  const navigate = useNavigate();
  const { id } = useParams();
  const [search] = useSearchParams();
  const selectedTier = search.get("tier") === "archive" ? "archive" : "live";
  return <SessionWorkspace selectedId={id} selectedTier={selectedTier} onSelect={(selected, tier) => { void navigate(`/sessions/${encodeURIComponent(selected)}${tier === "archive" ? "?tier=archive" : ""}`); }} />;
}
async function start() {
  const response = await fetch(new URL(".tether/config.json", document.baseURI), { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw new Error("UI configuration unavailable");
  const settings = config(await response.json());
  const client = new TetherClient({ baseUrl: settings.api_discovery_path.slice(0, -"/capabilities".length) });
  const root = document.getElementById("root");
  if (!root) throw new Error("Missing root element");
  createRoot(root).render(<StrictMode><ClientProvider client={client}><BrowserRouter basename={settings.base_path || "/"}><Routes><Route path="/" element={<Workspace />} /><Route path="/sessions/:id" element={<Workspace />} /><Route path="*" element={<p>Page not found. <a href={document.baseURI}>Return to sessions</a></p>} /></Routes></BrowserRouter></ClientProvider></StrictMode>);
}
void start().catch(() => {
  const root = document.getElementById("root");
  if (root) { root.textContent = "Tether could not start. Reload this page to reconnect."; root.setAttribute("role", "alert"); }
});
