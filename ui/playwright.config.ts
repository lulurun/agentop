import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  // Each run first clears what an interrupted one left, then its own.
  globalSetup: "./e2e/leftovers.ts",
  globalTeardown: "./e2e/leftovers.ts",
  timeout: 45_000,
  workers: 1,
  use: { viewport: { width: 1360, height: 820 } },
  reporter: [["list"]],
});
