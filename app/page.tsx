import { Hero } from "@/components/waitlist/Hero";
import { WaitlistNav } from "@/components/waitlist/WaitlistNav";
import { CapabilityCanvas } from "@/components/waitlist/CapabilityCanvas";
import {
  BuildSecuritiesSection,
  EventTriggersSection,
  FAQSection,
  HowItWorksSection,
  WaitlistFormBlock,
  WordmarkFooter,
} from "@/components/waitlist/Sections";

export default function WaitlistPage(): React.ReactElement {
  return (
    <main className="min-h-screen bg-white text-[#0d0d0e]">
      <WaitlistNav />
      <Hero />
      <HowItWorksSection />
      <section id="capabilities">
        <CapabilityCanvas />
      </section>
      <BuildSecuritiesSection />
      <EventTriggersSection />
      <FAQSection />
      <section id="waitlist">
        <WaitlistFormBlock />
      </section>
      <WordmarkFooter />
    </main>
  );
}
