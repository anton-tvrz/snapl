import { defineConfig, devices } from "@playwright/test";

// One browser check of the built UI, against the real server on captured
// fixtures (spec 011 research R16). It needs `npm run build` first.
const PORT = 18931;
// The look check's fixtures, every source up and a scan that recorded its
// steps: the longest default page, which SC-003 of spec 014 is measured on
// (e2e/layout.spec.ts).
const LOOK_PORT = 18933;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } }],
  webServer: [
    {
      command: `uv run python ../../../dev/scripts/serve_dashboard_fixtures.py --port ${PORT}`,
      url: `http://127.0.0.1:${PORT}/api/v1/mode`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `uv run python ../../../dev/scripts/serve_dashboard_fixtures.py --port ${LOOK_PORT} --sources up --scan-steps`,
      url: `http://127.0.0.1:${LOOK_PORT}/api/v1/mode`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
