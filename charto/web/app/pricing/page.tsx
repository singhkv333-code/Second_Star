import type { Metadata } from "next";
import { PublicPricing } from "@/components/pricing/PublicPricing";

export const metadata: Metadata = {
  title: "Pricing | Pivot",
  description: "Pivot plans and pricing in INR: start free, upgrade for more charting, research and strategy testing.",
};

export default function Page(): React.ReactElement {
  return <PublicPricing />;
}
