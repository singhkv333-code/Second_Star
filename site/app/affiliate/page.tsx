import { redirect } from "next/navigation";

/** /affiliate — the referral programme is a public page on the platform (no
 *  sign-in needed), where its terms and "Sign up" are served. */
export default function AffiliatePage(): never {
  redirect("https://app.pivotnow.in/affiliate?from=site");
}
