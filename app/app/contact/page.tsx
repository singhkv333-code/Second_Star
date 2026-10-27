import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  robots: { index: true, follow: true },
  title: "Contact | Pivot",
  description: "Reach the Pivot team for support, billing, privacy requests and complaints.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="contact" />;
}
