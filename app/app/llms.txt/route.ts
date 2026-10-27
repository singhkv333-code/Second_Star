import { getCompanies } from "@/lib/seo/data";
import { llmsTxt } from "@/lib/seo/markdown";

/** /llms.txt (llmstxt.org): what Pivot is and where its pages are, for
 *  language models and the agents that browse for them. */

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return new Response(llmsTxt(await getCompanies().catch(() => null)), {
    headers: { "Content-Type": "text/markdown; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
