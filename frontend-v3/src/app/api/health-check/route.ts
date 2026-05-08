import { NextResponse } from "next/server";

/**
 * Server-side health-check proxy for the Observability Settings tab.
 *
 * Browser → /api/health-check?layer=grafana
 *         → server fetches http://localhost:3001/api/health
 *         → returns { up: boolean }
 *
 * Avoids browser CORS blocks (Grafana/GlitchTip don't send
 * Access-Control-Allow-Origin to localhost:3000).
 */
const ENDPOINTS: Record<string, string> = {
  grafana: "http://localhost:3001/api/health",
  glitchtip: "http://localhost:8080/",
  prometheus: "http://localhost:9090/-/ready",
  loki: "http://localhost:3100/ready",
};

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const layer = url.searchParams.get("layer") ?? "";
  const target = ENDPOINTS[layer];
  if (!target) {
    return NextResponse.json({ up: false, error: "unknown layer" }, { status: 400 });
  }
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 3000);
    const r = await fetch(target, { cache: "no-store", signal: ctrl.signal });
    clearTimeout(timer);
    return NextResponse.json({ up: r.ok, status: r.status });
  } catch (e) {
    return NextResponse.json({ up: false, error: String(e) }, { status: 200 });
  }
}
