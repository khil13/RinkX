// Performance budget for the built site (run after `vite build`): gzip sizes of what a phone downloads.
import { readdirSync, readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BUDGET_KB = { entryJs: 135, totalJs: 200, css: 12 };
const dir = new URL("../dist/assets/", import.meta.url);
const html = readFileSync(new URL("../dist/index.html", import.meta.url), "utf8");
const kb = (f) => gzipSync(readFileSync(new URL(f, dir))).length / 1024;
const files = readdirSync(dir);
const entry = files.filter((f) => f.endsWith(".js") && html.includes(f));
const sizes = {
  entryJs: entry.reduce((a, f) => a + kb(f), 0),
  totalJs: files.filter((f) => f.endsWith(".js")).reduce((a, f) => a + kb(f), 0),
  css: files.filter((f) => f.endsWith(".css")).reduce((a, f) => a + kb(f), 0),
};
let ok = true;
for (const [k, limit] of Object.entries(BUDGET_KB)) {
  const over = sizes[k] > limit;
  ok &&= !over;
  console.log(`${over ? "OVER" : "ok  "} ${k.padEnd(8)} ${sizes[k].toFixed(1).padStart(6)} KB gzip (budget ${limit} KB)`);
}
if (!ok) process.exit(1);
