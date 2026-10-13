import type { Metadata } from "next";
import { CompanyPage } from "@/components/company/CompanyPage";

export const metadata: Metadata = {
  title: "Privacy policy | Pivot",
  description: "Learn how Pivot Tech handles account information, saved work, AI requests, analytics and payments.",
};

export default function Page(): React.ReactElement {
  return <CompanyPage page="privacy" />;
}
