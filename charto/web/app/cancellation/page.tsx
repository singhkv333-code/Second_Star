import { permanentRedirect } from "next/navigation";

/* The cancellation policy is part of the refund and cancellation policy. */
export default function Page(): never {
  permanentRedirect("/refunds");
}
