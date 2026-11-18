# Logo

`agentop.svg` is the only copy of the logo. To change the icon, replace this file: the builds export everything else from it, and the copies are not committed.

| Where | Made by | When |
|-------|---------|------|
| `ui/public/`: `logo.svg`, `favicon.ico`, `icon-192.png`, `icon-512.png`, `apple-touch-icon.png`, `icon-maskable-512.png` | `ui/scripts/icons.mjs` (`npm run icons` in `ui/`), with resvg-js | before `npm run build` and `npm run dev`, so `scripts/build-ui.sh`, a service update, and the release workflow |
| `desktop/src-tauri/icons/` | `tauri icon` (`npm run icons` in `desktop/`) | Tauri's `beforeBuildCommand` and `beforeDevCommand`, so `scripts/build-app.sh` and the desktop workflow |

The logo should be square and draw its own background. The apple-touch and maskable icons need an opaque square, so they put the logo on the colour at the middle of its top edge; the maskable one shrinks it to 80%, inside the circle Android crops to.

Running `cargo` directly in `desktop/src-tauri/` (tests, `cargo check`) needs the desktop icons: run `npm run icons` in `desktop/` once first.
