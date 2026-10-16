import { NextRequest, NextResponse } from "next/server";

/** A redirect by PATH. Behind nginx the standalone server sees itself as
 *  localhost:3000, so an absolute URL built from `request.url` sent real
 *  visitors to https://localhost:3000/... (seen 2026-10-16). A relative
 *  Location resolves against the address the visitor actually used. */
function redirectTo(path: string): NextResponse {
  return new NextResponse(null, { status: 307, headers: { Location: path } });
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ code: string }> },
): Promise<NextResponse> {
  const { code } = await context.params;
  const upstream = process.env.CHARTO_UPSTREAM || "http://127.0.0.1:5174";
  try {
    const response = await fetch(`${upstream}/referrals/visit?code=${encodeURIComponent(code)}`, {
      cache: "no-store",
    });
    if (response.ok) {
      const data = (await response.json()) as { token?: string; window_seconds?: number };
      if (data.token) {
        const redirect = redirectTo("/signup");
        redirect.cookies.set("pivot_referral", data.token, {
          httpOnly: true,
          sameSite: "lax",
          secure: request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto") === "https",
          path: "/",
          maxAge: data.window_seconds,
        });
        return redirect;
      }
    }
  } catch {
    // The programme remains closed when the attribution service is unavailable.
  }
  return redirectTo("/affiliate");
}
