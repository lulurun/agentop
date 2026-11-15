# Logo

`agentop.svg` is the original logo. Its text needs the Inter font, so the app uses copies with the text turned into outlines, in `ui/public/`:

| File | Use |
|------|-----|
| `logo.svg` | the logo with outlined text; the browser tab icon |
| `favicon.ico` | browser tab icon, 16, 32 and 48 px |
| `icon-192.png`, `icon-512.png` | home-screen icons in `manifest.webmanifest` |
| `icon-maskable-512.png` | Android's masked icon: the logo sits inside the safe circle |
| `apple-touch-icon.png` | iOS Add to Home Screen, 180 px |

The PNGs put the logo on the app's background, `#161b22`. To make them again after changing `agentop.svg` (needs `resvg`, `usvg` and ImageMagick):

```bash
usvg assets/logo/agentop.svg /tmp/outlined.svg
sed '1s|<svg width="256" height="242"|<svg viewBox="0 0 256 242" width="256" height="242" role="img" aria-label="Agentop"|' /tmp/outlined.svg > ui/public/logo.svg
mk() { resvg -w $(( $1 * $2 / 100 )) ui/public/logo.svg /tmp/l.png && magick -size ${1}x${1} xc:'#161b22' /tmp/l.png -gravity center -composite -strip PNG24:$3; }
mk 512 72 ui/public/icon-512.png
mk 192 72 ui/public/icon-192.png
mk 180 72 ui/public/apple-touch-icon.png
mk 512 56 ui/public/icon-maskable-512.png
resvg -w 256 ui/public/logo.svg /tmp/f.png
magick /tmp/f.png -background none -gravity center -extent 256x256 -define icon:auto-resize=48,32,16 ui/public/favicon.ico
```

The `sed` line assumes the original's `width="256" height="242"`; change it with the size.
