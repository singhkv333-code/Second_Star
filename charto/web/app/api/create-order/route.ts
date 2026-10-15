import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(_request?: NextRequest) {
  // Retired arbitrary-amount checkout never fulfilled a plan. Old tabs must
  // not collect another unfulfilled payment after the subscription rollout.
  return NextResponse.json({ error: "Use signed-in Pro/Pro+ checkout.",
    checkout_url: "https://app.pivotnow.in/pricing" }, {
    status: 410, headers: { "Cache-Control": "no-store" },
  });
}
