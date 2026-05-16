/**
 * lighthouse-baseline.mjs
 *
 * Runs Google Lighthouse against all 4 compliance-agent prod pages.
 * Auth-gated pages use the Supabase REST + localStorage injection pattern,
 * connecting over CDP to the same Chrome instance that Lighthouse will use.
 *
 * Pattern:
 *   1. chrome-launcher owns Chrome with a temp profile
 *   2. Playwright connects over CDP to inject localStorage auth
 *   3. Lighthouse runs against the same Chrome process/port
 *
 * Run: node --use-system-ca scripts/lighthouse-baseline.mjs
 */

import { launch as launchChrome } from "chrome-launcher";
import { chromium } from "playwright";
import lighthouse from "lighthouse";
import { writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import https from "https";
import os from "os";
import path from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── config ───────────────────────────────────────────────────────────────────

const BASE_URL = "https://compliance-agent-mu.vercel.app";
const SUPABASE_URL = "https://zcmdsblqbgatsrofptsq.supabase.co";
const SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpjbWRzYmxxYmdhdHNyb2ZwdHNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgzMTY0MzgsImV4cCI6MjA5Mzg5MjQzOH0.q6pZu7lnfnp3TkiMLV6RzyB_3f5f_A6TxRz1R5_dV3I";
const ADMIN_EMAIL = "admin@compliance-agent.local";
const ADMIN_PASSWORD = "Audit-Pass-2026-05-10!";

const PAGES = [
  { path: "/login", requiresAuth: false },
  { path: "/queue", requiresAuth: true },
  { path: "/tracker?tab=awaiting_review", requiresAuth: true },
  { path: "/rejections", requiresAuth: true },
];

const OUTPUT_DIR = join(__dirname, "..", "test-results");
const OUTPUT_JSON = join(OUTPUT_DIR, "lighthouse-baseline-2026-05-16.json");
const OUTPUT_MD = join(OUTPUT_DIR, "lighthouse-baseline-2026-05-16.md");

const CHROME_PATH = "C:/Program Files/Google/Chrome/Application/chrome.exe";

// LH desktop config
function makeLhConfig() {
  return {
    extends: "lighthouse:default",
    settings: {
      formFactor: "desktop",
      screenEmulation: {
        mobile: false,
        width: 1350,
        height: 940,
        deviceScaleFactor: 1,
        disabled: false,
      },
      throttlingMethod: "simulate",
      throttling: {
        rttMs: 40,
        throughputKbps: 10240,
        cpuSlowdownMultiplier: 1,
      },
      onlyCategories: ["performance"],
      maxWaitForLoad: 45000,
      maxWaitForFcp: 30000,
    },
  };
}

// ── helpers ──────────────────────────────────────────────────────────────────

function httpsPost(hostname, urlPath, headers, body) {
  return new Promise((resolve, reject) => {
    const bodyStr = JSON.stringify(body);
    const req = https.request(
      {
        hostname,
        path: urlPath,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(bodyStr),
          ...headers,
        },
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => (data += chunk));
        res.on("end", () => {
          if (res.statusCode >= 200 && res.statusCode < 300) {
            try {
              resolve(JSON.parse(data));
            } catch {
              resolve(data);
            }
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 200)}`));
          }
        });
      },
    );
    req.on("error", reject);
    req.write(bodyStr);
    req.end();
  });
}

async function getSupabaseSession() {
  const host = new URL(SUPABASE_URL).hostname;
  return httpsPost(
    host,
    "/auth/v1/token?grant_type=password",
    { apikey: SUPABASE_ANON_KEY },
    { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
  );
}

function extractMetrics(lhr) {
  const audits = lhr.audits;
  const categories = lhr.categories;
  const inpAudit = audits["interaction-to-next-paint"];
  const tbtAudit = audits["total-blocking-time"];
  const inpValue = inpAudit?.numericValue;
  const tbtValue = tbtAudit?.numericValue;

  return {
    LCP: Math.round(audits["largest-contentful-paint"]?.numericValue ?? 0),
    INP: Math.round(inpValue ?? tbtValue ?? 0),
    INP_label: inpValue != null ? "INP" : "TBT_proxy",
    CLS: parseFloat(
      (audits["cumulative-layout-shift"]?.numericValue ?? 0).toFixed(4),
    ),
    FCP: Math.round(audits["first-contentful-paint"]?.numericValue ?? 0),
    SI: Math.round(audits["speed-index"]?.numericValue ?? 0),
    perf_score: Math.round((categories?.performance?.score ?? 0) * 100),
  };
}

// ── benchmark one page ───────────────────────────────────────────────────────

async function benchmarkPage(pageDef, session, pageIndex) {
  const { path: pagePath, requiresAuth } = pageDef;
  const url = `${BASE_URL}${pagePath}`;
  const cdpPort = 9350 + pageIndex;

  console.log(`\n--- ${pagePath} (cdp :${cdpPort}) ---`);

  if (requiresAuth && !session) {
    console.log("  Skipping — auth required but no session");
    return {
      result: "auth_gated",
      note: `${pagePath}: skipped — Supabase auth unavailable`,
    };
  }

  // Use a unique temp user-data-dir per run so profiles don't collide
  const userDataDir = path.join(
    os.tmpdir(),
    `lh-profile-${Date.now()}-${pageIndex}`,
  );

  let chrome = null;
  try {
    // 1. Launch Chrome via chrome-launcher (owns the process)
    chrome = await launchChrome({
      port: cdpPort,
      chromePath: CHROME_PATH,
      chromeFlags: [
        "--headless=new",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        `--user-data-dir=${userDataDir}`,
      ],
      logLevel: "silent",
    });

    console.log(`  Chrome PID ${chrome.pid} listening on :${cdpPort}`);

    // 2. Connect Playwright over CDP to inject auth
    if (requiresAuth && session) {
      console.log("  Connecting Playwright over CDP for auth injection...");
      const browser = await chromium.connectOverCDP(
        `http://localhost:${cdpPort}`,
      );
      const contexts = browser.contexts();
      const ctx = contexts[0] ?? (await browser.newContext());
      const page = await ctx.newPage();

      // Go to /login to establish origin for localStorage
      await page.goto(`${BASE_URL}/login`, {
        waitUntil: "domcontentloaded",
        timeout: 30_000,
      });

      const projectRef = new URL(SUPABASE_URL).hostname.split(".")[0];
      const storageKey = `sb-${projectRef}-auth-token`;

      await page.evaluate(
        ({ key, sess }) => {
          window.localStorage.setItem(
            key,
            JSON.stringify({
              access_token: sess.access_token,
              refresh_token: sess.refresh_token,
              expires_at:
                sess.expires_at ??
                Math.floor(Date.now() / 1000) + (sess.expires_in ?? 3600),
              token_type: sess.token_type ?? "bearer",
              user: sess.user,
            }),
          );
        },
        { key: storageKey, sess: session },
      );

      // Navigate to the target page and let it settle
      try {
        await page.goto(url, { waitUntil: "networkidle", timeout: 50_000 });
      } catch {
        await page.goto(url, {
          waitUntil: "domcontentloaded",
          timeout: 30_000,
        });
      }
      await page.waitForTimeout(3000);

      const currentUrl = page.url();
      console.log(`  Playwright settled at: ${currentUrl}`);

      if (currentUrl.includes("/login")) {
        console.log(
          "  App redirected to /login — token injection not accepted",
        );
        await browser.disconnect();
        await chrome.kill();
        return {
          result: "auth_gated",
          note: `${pagePath}: app redirected to /login despite localStorage injection`,
        };
      }

      // Close Playwright connection — Chrome stays alive for Lighthouse
      // (browser.close() on a CDP-connected browser only disconnects, does not kill Chrome)
      await browser.close();
      console.log("  Playwright disconnected; Chrome still alive for LH");

      // Brief pause so Chrome stabilises before LH takes over
      await new Promise((r) => setTimeout(r, 1000));
    }

    // 3. Run Lighthouse against the same Chrome process
    console.log("  Running Lighthouse...");
    const lhRun = await lighthouse(
      url,
      {
        port: cdpPort,
        output: "json",
        logLevel: "error",
        disableStorageReset: true,
      },
      makeLhConfig(),
    );

    if (!lhRun?.lhr) {
      throw new Error("Lighthouse returned no lhr");
    }

    const lhr = lhRun.lhr;

    if (lhr.runtimeError) {
      throw new Error(
        `LH runtime error: ${lhr.runtimeError.code} — ${lhr.runtimeError.message?.slice(0, 200)}`,
      );
    }

    const metrics = extractMetrics(lhr);

    console.log(
      `  DONE — Score: ${metrics.perf_score} | LCP: ${metrics.LCP}ms | FCP: ${metrics.FCP}ms | ` +
        `${metrics.INP_label}: ${metrics.INP}ms | CLS: ${metrics.CLS} | SI: ${metrics.SI}ms`,
    );

    return {
      result: {
        LCP: metrics.LCP,
        INP: metrics.INP,
        INP_label: metrics.INP_label,
        CLS: metrics.CLS,
        FCP: metrics.FCP,
        SI: metrics.SI,
        perf_score: metrics.perf_score,
        final_url: lhr.finalDisplayedUrl,
        fetch_time: lhr.fetchTime,
      },
      note: null,
    };
  } catch (err) {
    const msg = String(err).slice(0, 400);
    console.error(`  ERROR: ${msg}`);
    return {
      result: { error: msg },
      note: `${pagePath}: error — ${msg.slice(0, 120)}`,
    };
  } finally {
    if (chrome) {
      try {
        await chrome.kill();
      } catch (killErr) {
        // Windows EPERM on temp dir cleanup is non-fatal — Chrome process is still killed
        const msg = String(killErr);
        if (!msg.includes("EPERM") && !msg.includes("Permission denied")) {
          console.warn(`  chrome.kill() warning: ${msg.slice(0, 100)}`);
        }
      }
    }
    // Let OS reclaim port/profile before next run
    await new Promise((r) => setTimeout(r, 2000));
  }
}

// ── main ─────────────────────────────────────────────────────────────────────

async function main() {
  const startMs = Date.now();
  console.log("=== Lighthouse Baseline 2026-05-16 ===");
  console.log(`Target:  ${BASE_URL}`);
  console.log(`Time:    ${new Date().toISOString()}`);
  console.log(`Chrome:  ${CHROME_PATH}`);

  mkdirSync(OUTPUT_DIR, { recursive: true });

  // Authenticate once
  console.log("\nAuthenticating via Supabase REST...");
  let session = null;
  let authError = null;
  try {
    session = await getSupabaseSession();
    console.log(`  OK — user: ${session?.user?.email ?? "unknown"}`);
  } catch (err) {
    authError = String(err);
    console.warn(`  FAILED: ${authError}`);
  }

  const results = {};
  const notes = [];

  for (let i = 0; i < PAGES.length; i++) {
    const { result, note } = await benchmarkPage(PAGES[i], session, i);
    results[PAGES[i].path] = result;
    if (note) notes.push(note);
  }

  const elapsedSec = ((Date.now() - startMs) / 1000).toFixed(1);
  const capturedAt = new Date().toISOString();

  // ── JSON ────────────────────────────────────────────────────────────────────
  const jsonOutput = {
    captured_at: capturedAt,
    deploy_sha: "ff4f2c0",
    deploy_id: "dpl_7ZDHGtqxsWzQeeV6n4VRcp866qjc",
    elapsed_sec: parseFloat(elapsedSec),
    results,
    notes:
      notes.length > 0
        ? notes.join(" | ")
        : "All pages benchmarked successfully",
  };

  writeFileSync(OUTPUT_JSON, JSON.stringify(jsonOutput, null, 2));
  console.log(`\nJSON:  ${OUTPUT_JSON}`);

  // ── Markdown ────────────────────────────────────────────────────────────────
  const tableRows = Object.entries(results)
    .map(([p, data]) => {
      if (typeof data === "string") {
        return `| \`${p}\` | — | — | — | — | — | — | ${data} |`;
      }
      if (data?.error) {
        return `| \`${p}\` | — | — | — | — | — | — | ERROR |`;
      }
      return `| \`${p}\` | ${data.perf_score} | ${data.LCP} | ${data.INP} (${data.INP_label}) | ${data.CLS} | ${data.FCP} | ${data.SI} | ok |`;
    })
    .join("\n");

  const md = `# Lighthouse Baseline — 2026-05-16

**Captured at:** ${capturedAt}
**Deploy SHA:** ff4f2c0
**Deploy ID:** dpl_7ZDHGtqxsWzQeeV6n4VRcp866qjc
**Target:** ${BASE_URL}
**Elapsed:** ${elapsedSec}s

## Results

| Page | Perf Score | LCP (ms) | INP/TBT (ms) | CLS | FCP (ms) | Speed Index (ms) | Status |
|------|-----------|---------|------------|-----|---------|-----------------|--------|
${tableRows}

## Notes

${notes.length > 0 ? notes.map((n) => `- ${n}`).join("\n") : "- All pages benchmarked without issues."}

---

## Re-run after redeploy

\`\`\`bash
node --use-system-ca scripts/lighthouse-baseline.mjs
\`\`\`

Delta target: LCP improvement > 200ms, Perf score +5 pts after wave-5 optimisations.
`;

  writeFileSync(OUTPUT_MD, md);
  console.log(`MD:    ${OUTPUT_MD}`);

  // ── stdout summary ──────────────────────────────────────────────────────────
  console.log("\n=== SUMMARY ===");
  console.log(
    "Page                               | Score | LCP   | INP/TBT | CLS    | FCP   | SI    | Status",
  );
  console.log("-".repeat(98));
  for (const [p, data] of Object.entries(results)) {
    const col = p.padEnd(34);
    if (typeof data === "string") {
      console.log(
        `${col} | —     | —     | —       | —      | —     | —     | ${data}`,
      );
    } else if (data?.error) {
      console.log(
        `${col} | —     | —     | —       | —      | —     | —     | ERROR`,
      );
    } else {
      console.log(
        `${col} | ${String(data.perf_score).padEnd(5)} | ${String(data.LCP).padEnd(5)} | ${String(data.INP).padEnd(7)} | ${String(data.CLS).padEnd(6)} | ${String(data.FCP).padEnd(5)} | ${String(data.SI).padEnd(5)} | ok`,
      );
    }
  }
  console.log(`\nTotal elapsed: ${elapsedSec}s`);
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
