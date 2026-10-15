import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  title: "Cancellation policy | Pivot",
  description: "How to cancel a paid Pivot plan, what happens when you do, and why cancelling does not refund.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="cancellation" />;
}
