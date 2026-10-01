import { defineConfig, devices } from "@playwright/test";

const python = process.env.RINKX_PYTHON ?? "python3";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: process.env.CI ? "github" : "list",
  use: { trace: "retain-on-failure" },
  // Plain static servers, like GitHub Pages: no rewrites, no server logic.
  webServer: [
    {
      command: `${python} -m http.server 4173 --bind 127.0.0.1 --directory tests/e2e/.sites/configured`,
      url: "http://127.0.0.1:4173/index.html",
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "ignore",
    },
    {
      command: `${python} -m http.server 4174 --bind 127.0.0.1 --directory tests/e2e/.sites/setup`,
      url: "http://127.0.0.1:4174/index.html",
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "ignore",
    },
  ],
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "iphone", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
});
