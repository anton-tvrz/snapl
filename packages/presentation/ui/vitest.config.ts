import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      // Generated from the contract, the entry point, and test support.
      exclude: ["src/api/schema.d.ts", "src/main.tsx", "src/test/**", "src/**/*.test.{ts,tsx}", "src/vite-env.d.ts"],
      // Principle III, as spec 011 FR-046 applies it to UI code.
      thresholds: { lines: 80 },
      reporter: ["text-summary", "text"],
    },
  },
});
