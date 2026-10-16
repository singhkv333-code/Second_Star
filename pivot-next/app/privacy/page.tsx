import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  robots: { index: true, follow: true },
  title: "Privacy policy | Pivot",
  description: "What personal data Pivot collects, why, who processes it, where it is stored, how long it is kept, and your rights under India's DPDP Act.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="privacy" />;
}
