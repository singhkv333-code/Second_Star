import { INDEXNOW_KEY } from "@/lib/seo/site";

/** The IndexNow ownership file (scripts/indexnow.mjs submits with
 *  keyLocation pointing here). */

export function GET(): Response {
  return new Response(INDEXNOW_KEY, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
