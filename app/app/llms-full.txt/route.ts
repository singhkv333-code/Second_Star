import { getCompanies } from "@/lib/seo/data";
import { llmsFullTxt } from "@/lib/seo/markdown";

/** /llms-full.txt: every covered company in one Markdown table. */

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return new Response(llmsFullTxt(await getCompanies().catch(() => null)), {
    headers: { "Content-Type": "text/markdown; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
