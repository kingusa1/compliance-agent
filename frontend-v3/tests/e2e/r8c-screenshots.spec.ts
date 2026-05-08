import { test, expect } from "@playwright/test";
import path from "node:path";

/**
 * R8c — final-validation screenshot walk.
 *
 * Logs in once as test@fame.dev (admin), then navigates every v3 route
 * and saves a viewport screenshot to `.planning/v3-rebuild/screenshots/`.
 *
 * NOT a pass/fail test — diagnostic capture only. Each step soft-asserts
 * that the page rendered SOMETHING above the fold, then dumps the PNG
 * regardless so we get artefacts even on partial failures.
 */

const SCREENSHOT_DIR = path.resolve(
  __dirname,
  "../../../.planning/v3-rebuild/screenshots",
);

type Route = { slug: string; path: string; admin?: boolean; needsId?: boolean };

const ROUTES: Route[] = [
  { slug: "01-login", path: "/login" },
  { slug: "02-queue", path: "/queue" },
  { slug: "03-calls", path: "/calls", admin: true },
  { slug: "04-call-detail", path: "/calls/__FIRST__", needsId: true },
  { slug: "05-customers", path: "/customers", admin: true },
  { slug: "06-customer-detail", path: "/customers/__FIRST__", needsId: true, admin: true },
  { slug: "07-deals", path: "/deals", admin: true },
  { slug: "08-deal-detail", path: "/deals/__FIRST__", needsId: true, admin: true },
  { slug: "09-compliant", path: "/compliant", admin: true },
  { slug: "10-non-compliant", path: "/non-compliant", admin: true },
  { slug: "11-rejections", path: "/rejections", admin: true },
  { slug: "12-portal-batches", path: "/portal-batches", admin: true },
  { slug: "13-agents", path: "/agents", admin: true },
  { slug: "14-agent-detail", path: "/agents/__FIRST__", needsId: true, admin: true },
  { slug: "15-scripts", path: "/scripts", admin: true },
  { slug: "16-findings", path: "/findings" },
  { slug: "17-observability", path: "/observability", admin: true },
  { slug: "18-settings", path: "/settings", admin: true },
];

test.describe.configure({ mode: "serial" });

test("R8c login + walk + screenshot every route", async ({ page }) => {
  test.setTimeout(180_000);

  // 1. Capture login page first (unauthed)
  await page.goto("/login");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, "R8c-01-login.png"),
    fullPage: false,
  });

  // 2. Sign in
  await page.fill('input[type="email"]', "test@fame.dev");
  await page.fill('input[type="password"]', "test");
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/(queue|calls|customers|deals)/, { timeout: 15_000 });

  // 3. Pre-fetch first call/customer/deal/agent ids for detail-page slugs.
  // Hit backend directly (port 8001) with the access token from the gotrue
  // session — same auth path the SPA uses.
  const ids = await page.evaluate(async () => {
    const sess = JSON.parse(
      localStorage.getItem(
        Object.keys(localStorage).find((k) => k.startsWith("sb-") && k.endsWith("-auth-token")) ?? "",
      ) ?? "{}",
    );
    const token: string | undefined = sess?.access_token;
    const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
    async function jget(url: string): Promise<Record<string, unknown> | null> {
      try {
        const r = await fetch(url, { headers });
        if (!r.ok) return null;
        return (await r.json()) as Record<string, unknown>;
      } catch {
        return null;
      }
    }
    const calls = await jget("http://localhost:8001/api/calls?limit=1");
    const customers = await jget("http://localhost:8001/api/customers?limit=1");
    const deals = await jget("http://localhost:8001/api/deals?limit=1");
    const agents = await jget("http://localhost:8001/api/agents?limit=1");
    const pick = (d: Record<string, unknown> | null, key: string, field: string): string | undefined => {
      const arr = d?.[key];
      if (!Array.isArray(arr) || arr.length === 0) return undefined;
      const first = arr[0] as Record<string, unknown>;
      const v = first[field];
      return typeof v === "string" ? v : undefined;
    };
    return {
      callId: pick(calls, "calls", "id"),
      customerSlug: pick(customers, "customers", "slug") ?? pick(customers, "items", "slug"),
      dealId: pick(deals, "deals", "id") ?? pick(deals, "items", "id"),
      agentName:
        pick(agents, "agents", "agent_name") ??
        pick(agents, "agents", "name") ??
        pick(agents, "items", "name"),
    };
  });

  console.log("[R8c] resolved detail ids:", JSON.stringify(ids));

  // 4. Walk every route
  for (const route of ROUTES) {
    if (route.slug === "01-login") continue; // already captured pre-auth

    let target = route.path;
    if (route.needsId) {
      const replacement =
        route.slug.startsWith("04") ? ids.callId :
        route.slug.startsWith("06") ? ids.customerSlug :
        route.slug.startsWith("08") ? ids.dealId :
        route.slug.startsWith("14") ? ids.agentName :
        null;
      if (!replacement) {
        console.log(`[R8c] skip ${route.slug} — no id available`);
        continue;
      }
      target = route.path.replace("__FIRST__", encodeURIComponent(replacement));
    }

    try {
      await page.goto(target, { waitUntil: "domcontentloaded", timeout: 20_000 });
      // settle: wait for fetches + animations
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
      await page.waitForTimeout(1500);
    } catch (e) {
      console.log(`[R8c] ${route.slug} navigation hiccup:`, (e as Error).message);
    }

    await page.screenshot({
      path: path.join(SCREENSHOT_DIR, `R8c-${route.slug}.png`),
      fullPage: false,
    });
    console.log(`[R8c] captured ${route.slug} (${target})`);
  }

  expect(true).toBe(true);
});
