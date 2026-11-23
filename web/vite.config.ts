import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `pnpm build` writes straight into the Python package, which `rex ui` serves.
// `pnpm dev` runs Vite with hot reload and forwards /api to a running `rex ui`.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../src/recipe_explorer/ui/static", emptyOutDir: true, chunkSizeWarningLimit: 1600 },
  server: { proxy: { "/api": "http://127.0.0.1:8002" } },
});
