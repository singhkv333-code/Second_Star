import { NextResponse, type NextRequest } from "next/server";

/** `/stock/RELIANCE.md` is the stock page as Markdown, served by
 *  app/md/stock/[symbol]. A dynamic segment cannot end in an extension, so
 *  the address is rewritten here; nothing else passes through this file. */
export function middleware(req: NextRequest): NextResponse {
  const m = /^\/stock\/([^/]+)\.md$/.exec(req.nextUrl.pathname);
  if (!m) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = `/md/stock/${m[1]}`;
  return NextResponse.rewrite(url);
}

export const config = { matcher: "/stock/:path*" };
