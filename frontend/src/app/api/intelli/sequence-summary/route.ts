import { NextResponse } from "next/server";

function candidateBackendBases(): string[] {
  const primary = (
    process.env.INTELLI_BACKEND_URL ??
    process.env.NEXT_PUBLIC_INTELLI_API_BASE ??
    ""
  )
    .trim()
    .replace(/\/$/, "");
  const defaults = ["http://127.0.0.1:8000", "http://localhost:8000"];
  const out: string[] = [];
  if (primary) out.push(primary);
  for (const d of defaults) {
    if (!out.includes(d)) out.push(d);
  }
  return out;
}

const SEQUENCE_PATHS = ["/api/sequence-summary", "/sequence-summary"] as const;

/**
 * Proxies to the FastAPI sequence endpoint. Tries each configured base URL
 * and each known path so older uvicorn builds still match.
 */
export async function GET() {
  const bases = candidateBackendBases();
  let lastStatus = 502;
  let lastBody = "";

  for (const base of bases) {
    for (const path of SEQUENCE_PATHS) {
      const url = `${base}${path}`;
      try {
        const res = await fetch(url, { cache: "no-store" });
        lastStatus = res.status;
        lastBody = await res.text();
        if (res.status === 404) {
          continue;
        }
        return new NextResponse(lastBody, {
          status: res.status,
          headers: {
            "content-type":
              res.headers.get("content-type") ?? "application/json; charset=utf-8",
          },
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        lastBody = JSON.stringify({
          detail: `Sequence proxy could not reach ${url}: ${msg}`,
        });
        lastStatus = 502;
      }
    }
  }

  return new NextResponse(lastBody || '{"detail":"Not Found"}', {
    status: lastStatus === 404 ? 404 : 502,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}
