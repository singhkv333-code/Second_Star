import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  title: "About Pivot",
  description: "Pivot is a market research platform for traders and investors in India: charts that explain themselves, company research and honest testing.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="about" />;
}
