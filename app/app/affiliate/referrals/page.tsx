import { redirect } from "next/navigation";

/** A referrer's link, referrals, commissions and payouts now live in the
 *  product under Settings → Affiliate; old links to this page land there. */
export default function Page(): never {
  redirect("/?settings=affiliate");
}
