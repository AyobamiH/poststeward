/** Raster exports from reviewed local SVGs. sharp is pinned by the root lock/override. */
import sharp from "sharp";
import { readFile, writeFile } from "node:fs/promises";
const icon = await readFile(new URL("../public/favicon.svg", import.meta.url));
const social = await readFile(new URL("../public/og-image.svg", import.meta.url));
const exportPng = (source, width, height) => sharp(source, { density: 192 }).resize(width, height).png({ compressionLevel: 9 }).toBuffer();
for (const [name, size] of [["apple-touch-icon.png", 180], ["icon-192.png", 192], ["icon-512.png", 512]]) {
  await writeFile(new URL("../public/" + name, import.meta.url), await exportPng(icon, size, size));
}
await writeFile(new URL("../public/og-image.png", import.meta.url), await exportPng(social, 1200, 630));
const sizes = [16, 32, 48], images = await Promise.all(sizes.map((size) => exportPng(icon, size, size)));
const header = Buffer.alloc(6 + sizes.length * 16); header.writeUInt16LE(1, 2); header.writeUInt16LE(sizes.length, 4);
let offset = header.length;
images.forEach((image, i) => {
  const at = 6 + i * 16; header[at] = sizes[i]; header[at + 1] = sizes[i];
  header.writeUInt16LE(1, at + 4); header.writeUInt16LE(32, at + 6);
  header.writeUInt32LE(image.length, at + 8); header.writeUInt32LE(offset, at + 12); offset += image.length;
});
await writeFile(new URL("../public/favicon.ico", import.meta.url), Buffer.concat([header, ...images]));
console.log("Exported five browser/social assets from the reviewed SVG sources.");
