import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useNavigate, useParams, useSearchParams, Routes, Route } from "react-router-dom";
import { TetherClient } from "@tether/client";
import { ClientProvider } from "@tether/react";
import { SessionWorkspace } from "@tether/ui";
import "@tether/ui/styles.css";
import "./reset.css";
function config(value) {
    if (typeof value !== "object" || value === null || !("base_path" in value) || !("api_discovery_path" in value))
        throw new Error("Invalid UI configuration");
    const { base_path: base, api_discovery_path: discovery } = value;
    if (typeof base !== "string" || typeof discovery !== "string" || !discovery.startsWith("/") || discovery.startsWith("//") || !discovery.endsWith("/capabilities"))
        throw new Error("Invalid API discovery path");
    return { base_path: base, api_discovery_path: discovery };
}
function Workspace() {
    const navigate = useNavigate();
    const { id } = useParams();
    const [search] = useSearchParams();
    const selectedTier = search.get("tier") === "archive" ? "archive" : "live";
    return _jsx(SessionWorkspace, { selectedId: id, selectedTier: selectedTier, onSelect: (selected, tier) => { void navigate(`/sessions/${encodeURIComponent(selected)}${tier === "archive" ? "?tier=archive" : ""}`); } });
}
async function start() {
    const response = await fetch(new URL(".tether/config.json", document.baseURI), { credentials: "same-origin", cache: "no-store" });
    if (!response.ok)
        throw new Error("UI configuration unavailable");
    const settings = config(await response.json());
    const client = new TetherClient({ baseUrl: settings.api_discovery_path.slice(0, -"/capabilities".length) });
    const root = document.getElementById("root");
    if (!root)
        throw new Error("Missing root element");
    createRoot(root).render(_jsx(StrictMode, { children: _jsx(ClientProvider, { client: client, children: _jsx(BrowserRouter, { basename: settings.base_path || "/", children: _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Workspace, {}) }), _jsx(Route, { path: "/sessions/:id", element: _jsx(Workspace, {}) }), _jsx(Route, { path: "*", element: _jsxs("p", { children: ["Page not found. ", _jsx("a", { href: document.baseURI, children: "Return to sessions" })] }) })] }) }) }) }));
}
void start().catch(() => {
    const root = document.getElementById("root");
    if (root) {
        root.textContent = "Tether could not start. Reload this page to reconnect.";
        root.setAttribute("role", "alert");
    }
});
//# sourceMappingURL=main.js.map