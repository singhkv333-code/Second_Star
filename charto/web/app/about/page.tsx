import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  title: "About Pivot | Pivot",
  description: "Meet Pivot Tech: charts, market research, strategy testing and paper trading in one workspace.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="about" />;
}
