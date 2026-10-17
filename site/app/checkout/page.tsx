import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default async function CheckoutPage({ searchParams }: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const q = await searchParams;
  const plan = q.plan;
  if (plan !== "pro" && plan !== "pro_plus") redirect("https://app.pivotnow.in/pricing");
  const cycle = q.cycle === "monthly" ? "monthly" : "annual";
  redirect(`https://app.pivotnow.in/checkout?${new URLSearchParams({ plan, cycle })}`);
}
