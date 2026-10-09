/**
 * The chart (charto/preview), served by this app at /chart-app.
 *
 * The chart is ~26k lines of vanilla JS over a patched Lightweight Charts
 * build; it is mounted, not ported (see components/chart/ChartFrame.tsx). It
 * used to need a server of its own (charto/preview/serve.py on :5173) that the
 * shell proxied to, which made "start Pivot" mean starting a fifth process and
 * gave the chart its own idea of routing. Serving its files from here makes
 * the shell the one front door; in production nginx serves the same files
 * from the release before a request ever reaches this route.
 *
 * Files only, read-only, and never outside the chart's directory. `no-cache`
 * like nginx: every <script> carries a ?v= stamp, and an unstamped asset must
 * still revalidate rather than linger.
 */
import { promises as fs } from "node:fs";
import path from "node:path";

export const dynamic = "force-dynamic";

const ROOT = path.resolve(process.env.CHARTO_PREVIEW_DIR || path.join(process.cwd(), "..", "charto", "preview"));

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml",
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
  ".gif": "image/gif", ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff",
  ".ttf": "font/ttf", ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg",
  ".wav": "audio/wav", ".txt": "text/plain; charset=utf-8", ".map": "application/json",
};

async function serve(req: Request, parts: string[] | undefined, head: boolean): Promise<Response> {
  const url = new URL(req.url);
  if (!parts || parts.length === 0) {
    // Next strips a trailing slash, so the directory form cannot be the page's
    // address; index.html is (its ./js/… paths then resolve under /chart-app/).
    return Response.redirect(new URL("/chart-app/index.html" + url.search, url), 307);
  }
  const target = path.resolve(ROOT, ...parts.map((p) => decodeURIComponent(p)));
  if (target !== ROOT && !target.startsWith(ROOT + path.sep)) return new Response("Not found", { status: 404 });
  let file = target;
  try {
    const st = await fs.stat(file);
    if (st.isDirectory()) file = path.join(file, "index.html");
    const body = head ? null : await fs.readFile(file);
    return new Response(body, {
      status: 200,
      headers: {
        "Content-Type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream",
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return new Response("Not found", { status: 404 });
  }
}

type Ctx = { params: Promise<{ path?: string[] }> };

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
  return serve(req, (await ctx.params).path, false);
}

export async function HEAD(req: Request, ctx: Ctx): Promise<Response> {
  return serve(req, (await ctx.params).path, true);
}
