/* Headless smoke + mobile-layout test. Serves the repo so ../Editor resolves,
 * blocks the Pyodide CDN (too heavy / offline in CI) and asserts the app SHELL
 * renders: header, both mode tabs, the ISO tab shows its open-or-blocked card, and
 * — the key mobile regression guard — NO horizontal overflow at 320 px and 360 px.
 * Self-skips (exit 0) if playwright/Chromium isn't installed. Pyodide-driven flows
 * are covered by iso_roundtrip.py, which needs no browser. */
import http from "http";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");   // repo root (so /web and /Editor both serve)

let chromium;
try { ({ chromium } = await import("playwright")); }
catch { try { ({ chromium } = await import("playwright-core")); } catch {} }
if (!chromium) { console.log("SKIP e2e: playwright not installed."); process.exit(0); }

const MIME = { ".html":"text/html", ".js":"text/javascript", ".css":"text/css",
  ".json":"application/json", ".webmanifest":"application/manifest+json", ".png":"image/png", ".py":"text/plain" };
const server = http.createServer((req, res) => {
  const p = path.join(root, decodeURIComponent(req.url.split("?")[0]));
  if (!p.startsWith(root) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.statusCode = 404; return res.end(); }
  res.setHeader("Content-Type", MIME[path.extname(p)] || "application/octet-stream");
  fs.createReadStream(p).pipe(res);
});
await new Promise((r) => server.listen(0, r));
const port = server.address().port;
const url = `http://127.0.0.1:${port}/web/index.html`;

let browser, fail = 0, n = 0;
const ok = (name, cond, extra) => { n++; if (!cond) { fail++; console.error(`FAIL ${name}${extra?"  "+extra:""}`); } else console.log(`PASS ${name}`); };

try {
  browser = await chromium.launch();
} catch (e) { console.log("SKIP e2e: could not launch Chromium (" + e.message + ")."); server.close(); process.exit(0); }

try {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  // block the heavy Pyodide CDN — we only test the shell + layout here
  await page.route(/jsdelivr\.net|\/pyodide\//, (r) => r.abort());

  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".mode-tab", { timeout: 5000 });

  ok("header renders", await page.locator("header b").count() > 0);
  ok("two mode tabs", await page.locator(".mode-tab").count() === 2);

  // switch to the ISO tab → either the open card or the desktop-only blocked card is shown
  await page.locator('.mode-tab[data-mode="iso"]').click();
  const isoVisible = await page.evaluate(() => {
    const blocked = document.getElementById("isoBlocked");
    const open = document.getElementById("isoOpen");
    const shown = (el) => el && !el.classList.contains("hidden") && el.offsetParent !== null;
    return shown(blocked) || shown(open) || !!document.querySelector('.mode-pane[data-mode="iso"]:not([hidden])');
  });
  ok("ISO pane shows a card", isoVisible);

  // no horizontal overflow on small screens (the classic mobile regression)
  for (const w of [320, 360]) {
    await page.setViewportSize({ width: w, height: 720 });
    await page.locator('.mode-tab[data-mode="save"]').click();
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    ok(`no horizontal overflow @${w}px`, overflow <= 1, `overflow=${overflow}px`);
  }

  ok("no uncaught shell errors", pageErrors.length === 0, pageErrors.join(" | "));
} catch (e) {
  console.error("e2e error:", e.message); fail++;
} finally {
  if (browser) await browser.close();
  server.close();
}

console.log(`\n${n - fail}/${n} passed`);
process.exit(fail ? 1 : 0);
