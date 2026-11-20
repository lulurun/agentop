import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vitest/config";

// In the page itself rather than a service header, so the desktop app gets the
// same policy. Build-only: the dev server needs inline scripts. connect-src is
// open because the user chooses which services to connect to; tokens are in
// localStorage, so script-src is what protects them.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src ipc: http: https: ws: wss:", // ipc: is how the desktop window reaches its own commands
].join("; ");

function contentSecurityPolicy(): Plugin {
  return {
    name: "content-security-policy",
    apply: "build",
    transformIndexHtml: (html) =>
      html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
  };
}

// From git, so a build between releases says so (v2.3.0-2-gabc1234); pyproject.toml
// when there is no git, as in a source archive.
function uiVersion(): string {
  try {
    return execFileSync("git", ["describe", "--tags", "--match", "v[0-9]*", "--dirty"], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    const found = /^version = "(.+)"/m.exec(readFileSync(new URL("../pyproject.toml", import.meta.url), "utf8"));
    return found ? `v${found[1]}` : "unknown";
  }
}

export default defineConfig({
  define: { __UI_VERSION__: JSON.stringify(uiVersion()) },
  // Relative, so the same build works at /app, behind a proxy path, or in Tauri.
  base: "./",
  plugins: [react(), contentSecurityPolicy()],
  // No inlining: small font subsets would become data: URLs, which the CSP refuses.
  build: { outDir: "../src/agentop/service/webdist", emptyOutDir: true, assetsInlineLimit: 0 },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
