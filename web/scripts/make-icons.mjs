// Renders public/icon.svg to the PNG icons iOS and Android need (run once after changing the SVG):
//   node scripts/make-icons.mjs
import { readFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const svg = readFileSync(new URL("../public/icon.svg", import.meta.url), "utf8");
const browser = await chromium.launch();
const page = await browser.newPage();
const out = [
  ["apple-touch-icon.png", 180, 0],
  ["icon-192.png", 192, 0],
  ["icon-512.png", 512, 0],
  ["icon-maskable-512.png", 512, 0.12], // padded: Android may crop to a circle
];
for (const [name, size, pad] of out) {
  await page.setViewportSize({ width: size, height: size });
  const inner = Math.round(size * (1 - 2 * pad));
  await page.setContent(
    `<html><body style="margin:0;background:#0B0F14;display:grid;place-items:center;width:${size}px;height:${size}px">` +
      `<div style="width:${inner}px;height:${inner}px">${svg.replace("<svg ", `<svg width="${inner}" height="${inner}" `)}</div></body></html>`,
  );
  await page.screenshot({ path: new URL(`../public/${name}`, import.meta.url).pathname, omitBackground: false });
}
await browser.close();
