/**
 * Next.js API route: GET /api/overlay/[jobId]
 *
 * Acts as a thin proxy to the FastAPI backend so the browser never needs to
 * reach the backend directly (avoids CORS issues in deployment).
 */

import { NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function GET(
  _req: NextRequest,
  { params }: { params: { jobId: string } },
) {
  const { jobId } = params;

  try {
    const res = await fetch(`${BACKEND}/overlay/${jobId}`, {
      headers: { Accept: "application/json" },
      // Disable Next.js fetch caching for live status polling
      cache: "no-store",
    });

    const body = await res.json();
    return NextResponse.json(body, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { detail: "Backend unreachable", error: String(err) },
      { status: 502 },
    );
  }
}
