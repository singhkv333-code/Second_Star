/**
 * Development only: hand the signed-in Pivot user a chart (the dataserver) session
 * without a second sign-in.
 *
 * Billing and the chart live on the dataserver's accounts, which are separate from
 * Pivot's until the shared session lands (roadmap step 2). For the one dev
 * account named in .env.local, this signs into the dataserver server side, creating
 * the dataserver account on first use, and returns the token. The password stays
 * on the server; the browser only proves it holds that user's Pivot session.
 *
 *   DEV_PIVOT_EMAIL=dev@catalog.com
 *   DEV_PIVOT_PASSWORD=...
 *
 * Outside `next dev`, or without both variables, it answers 404.
 */

import { NextResponse, type NextRequest } from "next/server";

const BACKEND = process.env.PIVOT_BACKEND_ORIGIN || "http://127.0.0.1:8000";
const PIVOT_DATA = process.env.PIVOT_DATA_ORIGIN || "http://127.0.0.1:5174";

type DataserverAuth = { token?: string; error?: string };

async function dataserverAuth(path: "/auth/login" | "/auth/signup", body: Record<string, string>): Promise<DataserverAuth & { status: number }> {
  const r = await fetch(`${PIVOT_DATA}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const data = (await r.json().catch(() => ({}))) as DataserverAuth;
  return { ...data, status: r.status };
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const email = process.env.DEV_PIVOT_EMAIL?.trim().toLowerCase();
  const password = process.env.DEV_PIVOT_PASSWORD;
  if (process.env.NODE_ENV !== "development" || !email || !password) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }

  // Only the dev account's own Pivot session may borrow its chart session.
  const auth = req.headers.get("authorization") || "";
  const me = await fetch(`${BACKEND}/auth/me`, { headers: { Authorization: auth }, cache: "no-store" })
    .then((r) => (r.ok ? (r.json() as Promise<{ email?: string }>) : null))
    .catch(() => null);
  if (!me?.email || me.email.toLowerCase() !== email) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }

  let res = await dataserverAuth("/auth/login", { email, password });
  if (!res.token) {
    // First use: the account does not exist on the chart server yet.
    const made = await dataserverAuth("/auth/signup", { email, password, name: email.split("@")[0] ?? email });
    if (made.token) res = made;
  }
  if (!res.token) {
    return NextResponse.json(
      { error: `The chart server refused ${email}: ${res.error ?? res.status}. Check DEV_PIVOT_PASSWORD.` },
      { status: 502 },
    );
  }
  return NextResponse.json({ token: res.token });
}
