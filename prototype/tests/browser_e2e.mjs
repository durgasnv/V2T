// Browser end-to-end test: a real Chromium with a FAKE microphone that "speaks" a WAV file.
// Tests the demo page and the extension on a separate website.
//
// Needs:  npm i playwright-core   and a Playwright Chromium (npx playwright install chromium)
// Run:    node tests/browser_e2e.mjs <path-to-chrome>     (dictation server must be running with
//         ALLOWED_ORIGINS=http://127.0.0.1:8000 so the "other website" can connect)
import { chromium } from "playwright-core";
import { createServer } from "node:http";
import { readFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const chrome = process.argv[2];
const wav = join(here, "messy.wav");
const wavSeconds = (readFileSync(wav).length - 44) / 32000;     // 16 kHz mono 16-bit
const results = [];
const check = (name, ok, info) => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${info ? "  →  " + info : ""}`); };

async function dictate(page, selector) {
  await page.click(selector);
  await page.keyboard.down("Control"); await page.keyboard.down("Shift"); await page.keyboard.down("Space");
  await page.waitForTimeout((wavSeconds + 0.8) * 1000);       // "speak" the whole clip
  await page.keyboard.up("Space"); await page.keyboard.up("Shift"); await page.keyboard.up("Control");
}

const args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
              `--use-file-for-fake-audio-capture=${wav}`, "--autoplay-policy=no-user-gesture-required"];

// ---- 1. Demo page --------------------------------------------------------------------------
{
  const browser = await chromium.launch({ executablePath: chrome, args });
  const page = await browser.newPage();
  page.on("pageerror", (e) => console.log("page error:", e.message));
  await page.goto("http://localhost:8765/");
  await page.fill("#box", "Note:");
  await dictate(page, "#box");
  await page.waitForFunction(() => document.querySelector("#t-insert").textContent.includes("ms"), null, { timeout: 60000 });
  const box = await page.inputValue("#box");
  check("demo page: text typed into textarea", /tuesday/i.test(box) && box.startsWith("Note: "), JSON.stringify(box));
  check("demo page: raw text shown", /\S/.test(await page.textContent("#v-asr")), await page.textContent("#v-asr"));
  check("demo page: cleaned text + timing shown", /ms/.test(await page.textContent("#t-clean")),
        `${await page.textContent("#v-clean")} (${await page.textContent("#t-clean")})`);

  // Cleanup off → raw text inserted.
  await page.uncheck("#cleanup"); await page.fill("#box", "");
  await dictate(page, "#box");
  await page.waitForFunction(() => document.querySelector("#box").value.length > 0, null, { timeout: 60000 });
  check("demo page: cleanup toggle off → raw text", (await page.textContent("#v-clean")).startsWith("Off"),
        await page.inputValue("#box"));

  // Esc cancels: nothing typed.
  await page.fill("#box", "");
  await page.click("#box");
  await page.keyboard.down("Control"); await page.keyboard.down("Shift"); await page.keyboard.down("Space");
  await page.waitForTimeout(1500);
  await page.keyboard.press("Escape");
  await page.keyboard.up("Space"); await page.keyboard.up("Shift"); await page.keyboard.up("Control");
  await page.waitForTimeout(4000);
  check("demo page: Esc cancels (nothing typed)", (await page.inputValue("#box")) === "");
  await browser.close();
}

// ---- 2. Extension on a different website (contenteditable + textarea) ----------------------
{
  const site = createServer((_, res) => {
    res.setHeader("content-type", "text/html");
    res.end(`<!doctype html><title>Other site</title><textarea id="t"></textarea>
             <div id="ce" contenteditable="true" style="min-height:40px;border:1px solid #ccc"></div>`);
  }).listen(8000, "127.0.0.1");
  const ctx = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "ld-")), {
    executablePath: chrome, headless: true,
    args: [...args, `--disable-extensions-except=${join(here, "..", "extension")}`,
           `--load-extension=${join(here, "..", "extension")}`],
  });
  await ctx.grantPermissions(["microphone"], { origin: "http://127.0.0.1:8000" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("page error:", e.message));
  await page.goto("http://127.0.0.1:8000/");
  await page.waitForFunction(() => document.documentElement.hasAttribute("data-local-dictation"), null, { timeout: 10000 })
    .catch(() => {});
  check("extension: injected into other website",
        await page.evaluate(() => document.documentElement.hasAttribute("data-local-dictation")));
  await dictate(page, "#t");
  await page.waitForFunction(() => document.querySelector("#t").value.length > 0, null, { timeout: 60000 }).catch(() => {});
  check("extension: textarea dictation", /tuesday/i.test(await page.inputValue("#t")), await page.inputValue("#t"));
  await dictate(page, "#ce");
  await page.waitForFunction(() => document.querySelector("#ce").innerText.length > 0, null, { timeout: 60000 }).catch(() => {});
  check("extension: contenteditable dictation", /tuesday/i.test(await page.innerText("#ce")), await page.innerText("#ce"));
  await ctx.close(); site.close();
}

console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`);
process.exit(results.every(Boolean) ? 0 : 1);
