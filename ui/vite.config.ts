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
  "connect-src http: https: ws: wss:",
].join("; ");

function contentSecurityPolicy(): Plugin {
  return {
    name: "content-security-policy",
    apply: "build",
    transformIndexHtml: (html) =>
      html.replace("<head>", `<head>\n    <meta http-equiv="Content-Security-Policy" content="${CSP}" />`),
  };
}

export default defineConfig({
  // Relative, so the same build works at /app, behind a proxy path, or in Tauri.
  base: "./",
  plugins: [react(), contentSecurityPolicy()],
  // No inlining: small font subsets would become data: URLs, which the CSP refuses.
  build: { outDir: "../src/agentop/service/webdist", emptyOutDir: true, assetsInlineLimit: 0 },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
