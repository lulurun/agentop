// Export the app's icons from assets/logo/agentop.svg, the only copy of the logo, into public/.
import { Resvg } from "@resvg/resvg-js";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = fileURLToPath(new URL("../../assets/logo/agentop.svg", import.meta.url));
const out = (name) => fileURLToPath(new URL(`../public/${name}`, import.meta.url));
const svg = readFileSync(source, "utf8").replace(/<\?xml[^>]*>/, "");

const render = (markup, size) => new Resvg(markup, { fitTo: { mode: "width", value: size } }).render();
const png = (markup, size) => render(markup, size).asPng();

// iOS and Android's masks need an opaque square: fill it with the logo's own
// background, read from the middle of its top edge.
const sample = render(svg, 64);
const at = (64 + 32) * 4;
const background = `rgb(${sample.pixels[at]},${sample.pixels[at + 1]},${sample.pixels[at + 2]})`;

function onSquare(scale) {
  const inset = (512 * (1 - scale)) / 2;
  const inner = svg.replace("<svg", `<svg x="${inset}" y="${inset}" width="${512 * scale}" height="${512 * scale}"`);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" fill="${background}"/>${inner}</svg>`;
}

// An ICO of PNG images, which every current browser reads.
function ico(sizes) {
  const images = sizes.map((size) => png(svg, size));
  const header = Buffer.alloc(6 + 16 * images.length);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  let offset = header.length;
  images.forEach((image, i) => {
    const entry = 6 + 16 * i;
    header.writeUInt8(sizes[i] % 256, entry);
    header.writeUInt8(sizes[i] % 256, entry + 1);
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(image.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += image.length;
  });
  return Buffer.concat([header, ...images]);
}

copyFileSync(source, out("logo.svg"));
writeFileSync(out("favicon.ico"), ico([16, 32, 48]));
writeFileSync(out("icon-192.png"), png(svg, 192));
writeFileSync(out("icon-512.png"), png(svg, 512));
writeFileSync(out("apple-touch-icon.png"), png(onSquare(1), 180));
// Android crops a maskable icon to a circle of 80% of its width.
writeFileSync(out("icon-maskable-512.png"), png(onSquare(0.8), 512));
