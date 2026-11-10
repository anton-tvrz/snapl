import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands inside the Python package, git-ignored, so the wheel ships
// it and `snapl dashboard` never needs Node (spec 011 research R2).
const OUT_DIR = "../snapl_presentation/dashboard/static/ui";
const API = `http://127.0.0.1:${process.env.DASHBOARD_PORT ?? "18030"}`;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: OUT_DIR,
    emptyOutDir: true,
    // No source maps in the wheel: they would double its size.
    sourcemap: false,
  },
  server: {
    // `invoke dashboard.dev`: the page from Vite, the API from `snapl dashboard`.
    proxy: { "/api": API, "/metrics": API },
  },
});
