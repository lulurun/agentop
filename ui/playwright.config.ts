import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 45_000,
  workers: 1,
  use: { viewport: { width: 1360, height: 820 } },
  reporter: [["list"]],
});
