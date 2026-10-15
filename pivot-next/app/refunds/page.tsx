import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  title: "Refund policy | Pivot",
  description: "Pivot payments are final, except where a charge went wrong because of a failure on our side.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="refunds" />;
}
