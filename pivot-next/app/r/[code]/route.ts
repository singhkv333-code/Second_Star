import { NextRequest, NextResponse } from "next/server";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ code: string }> },
): Promise<NextResponse> {
  const { code } = await context.params;
  const destination = new URL("/signup", request.url);
  const upstream = process.env.CHARTO_UPSTREAM || "http://127.0.0.1:5174";
  try {
    const response = await fetch(`${upstream}/referrals/visit?code=${encodeURIComponent(code)}`, {
      cache: "no-store",
    });
    if (response.ok) {
      const data = (await response.json()) as { token?: string; window_seconds?: number };
      if (data.token) {
        const redirect = NextResponse.redirect(destination);
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
  return NextResponse.redirect(new URL("/affiliate", request.url));
}
