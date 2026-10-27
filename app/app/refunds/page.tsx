import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  robots: { index: true, follow: true },
  title: "Refund and cancellation policy | Pivot",
  description: "How to cancel a paid Pivot plan, and why payments are final except where a failure on our side caused the charge.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="refunds" />;
}
