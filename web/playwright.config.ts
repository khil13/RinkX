import { defineConfig, devices } from "@playwright/test";

const python = process.env.RINKX_PYTHON ?? "python3";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: process.env.CI ? "github" : "list",
  use: { trace: "retain-on-failure" },
  // Plain static servers, like GitHub Pages: no rewrites, no server logic.
  webServer: (
    [
      [4173, "league"],
      [4174, "setup"],
      [4175, "empty"],
      [4176, "models"],
    ] as const
  ).map(([port, site]) => ({
    command: `${python} -m http.server ${port} --bind 127.0.0.1 --directory tests/e2e/.sites/${site}`,
    url: `http://127.0.0.1:${port}/index.html`,
    reuseExistingServer: false,
    stdout: "ignore" as const,
    stderr: "ignore" as const,
  })),
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "iphone", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
});
