import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";
import { resolve } from "node:path";
export default defineConfig({
  root: resolve(import.meta.dirname), base: "./", plugins: [react(), tailwind()],
  build: { outDir: "../../packages/web-ui/src/tether_web_ui/assets", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
