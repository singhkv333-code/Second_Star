import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  title: "Terms & conditions | Pivot",
  description: "Read the terms for using Pivot, including analysis, simulated trading, accounts and payments.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="terms" />;
}
