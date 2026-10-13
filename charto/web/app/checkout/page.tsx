import { RazorpayCheckout } from "@/components/RazorpayCheckout";

export const dynamic = "force-dynamic";

export default function CheckoutPage() {
  return <RazorpayCheckout testMode={process.env.RAZORPAY_KEY_ID?.startsWith("rzp_test_") ?? true}
    configured={!!(process.env.RAZORPAY_KEY_ID && process.env.RAZORPAY_KEY_SECRET)} />;
}
