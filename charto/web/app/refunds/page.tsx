import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  title: "Refund and cancellation policy | Pivot",
  description: "How to cancel a paid Pivot plan, when payments are refunded, and how long refunds take.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="refunds" />;
}
