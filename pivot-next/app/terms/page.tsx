import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  robots: { index: true, follow: true },
  title: "Terms of service | Pivot",
  description: "The terms for using Pivot: accounts, what Pivot is and is not, data limits, billing, renewals and complaints.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="terms" />;
}
