export type CompanyPageKey = "about" | "terms" | "privacy";

export interface CompanySection {
  id: string;
  title: string;
  paragraphs: string[];
}

export interface CompanyContent {
  title: string;
  introduction: string;
  sections: CompanySection[];
}

export const COMPANY_NAME = "Pivot Tech";
export const CONTACT_EMAIL = "info@pivotnow.in";
export const UPDATED_DATE = "11 October 2026";

export const COMPANY_PAGES: Record<CompanyPageKey, CompanyContent> = {
  about: {
    title: "About Pivot",
    introduction: "Pivot is a financial analysis platform built by Pivot Tech for traders and investors in India. It brings charts, company research, strategy testing and paper trading into one workspace.",
    sections: [
      {
        id: "why-pivot", title: "Why we built Pivot",
        paragraphs: ["A market question rarely ends at a price chart. It can lead to a company filing, a comparison, a trading rule or a portfolio decision. Pivot brings these parts together so your research can stay connected.", "You can ask questions in plain language, explore charts and fundamentals, build a strategy and see how it behaves in a backtest or a simulated paper book."],
      },
      {
        id: "what-we-build", title: "What you can do",
        paragraphs: ["Use Pivot to explore charts, indicators and annotations, investigate company fundamentals and filings, compare securities and screen markets. You can ask questions in plain language while working with the data.", "Strategy tools help you define trading rules, examine historical results and follow trades in a simulated paper book. These tools are intended to help you understand and test an idea before making your own decisions."],
      },
      {
        id: "our-approach", title: "How we approach analysis",
        paragraphs: ["AI should help you examine a market, not make an uncertain answer sound certain. We build around real data, visible sources and clear limits. A backtest should expose an idea’s weaknesses as well as its strengths.", "Pivot is an early-stage product from Pivot Tech. Features and coverage continue to develop, and we welcome feedback on what is useful, unclear or missing."],
      },
      {
        id: "our-role", title: "What Pivot provides",
        paragraphs: ["Pivot provides tools for analysis and learning. We are not a broker or a registered investment adviser. Strategies and orders in the paper book are simulations; Pivot does not place live broker orders.", "Market analysis can be wrong, and historical performance does not guarantee future results. This is analysis, not financial advice."],
      },
    ],
  },
  terms: {
    title: "Terms & conditions",
    introduction: "These terms explain how you can use Pivot and what you can expect from us. Pivot is operated by Pivot Tech. By using the service, you agree to these terms.",
    sections: [
      {
        id: "using-pivot", title: "1. Using Pivot",
        paragraphs: ["These terms cover pivotnow.in and the Pivot services linked to it. You must be at least 18 and able to enter a binding agreement to create an account or purchase access. If you use Pivot for an organisation, you must have permission to act for it.", "Provide accurate account information, protect your sign-in details and tell us if you believe someone has accessed your account without permission. You are responsible for activity you authorise through your account."],
      },
      {
        id: "analysis-and-simulation", title: "2. Analysis and simulated trading",
        paragraphs: ["Pivot provides charts, market research, AI assistance, strategy tools, backtests and paper trading. Available features depend on your access and data coverage. Pivot is not a broker or a registered investment adviser, and its output is not personalised financial advice.", "Paper trades, balances and strategy fills are simulated. They do not buy or sell securities or create real investment returns. Paying for Pivot buys software access or the item described at checkout; it does not fund a trading account.", "Backtests depend on historical data and modelling assumptions. They may not reflect liquidity, slippage, fees or the conditions of a real trade. Past or simulated performance does not guarantee future results."],
      },
      {
        id: "data-and-ai", title: "3. Data and AI have limits",
        paragraphs: ["Prices, filings and other information can be delayed, incomplete or incorrect. Data availability and sources may change. AI responses can also contain errors. Check important information against the underlying source before relying on it.", "We do not guarantee a trading outcome, uninterrupted availability or the accuracy of every response. You remain responsible for your financial decisions and for seeking qualified advice when needed."],
      },
      {
        id: "fair-use", title: "4. Use the platform responsibly", paragraphs: ["Use Pivot lawfully and respect other users and our data providers. Do not attempt to access another person’s account, bypass access controls, disrupt the service or use it for fraud or market manipulation.", "Only submit material you have permission to use. Do not redistribute licensed market data without permission, abuse automated requests or attempt to extract confidential information, credentials or private user data."],
      },
      {
        id: "your-work", title: "5. Your work and our software",
        paragraphs: ["You retain your rights in the content you submit, including notes and strategy ideas. You give Pivot permission to store and process that content as needed to provide the service, subject to our Privacy Policy.", "Pivot’s software, branding and original materials belong to Pivot Tech or their respective owners. Your access permits you to use the service; it does not transfer ownership of our software or third-party data."],
      },
      {
        id: "payments", title: "6. Payments and access",
        paragraphs: ["Before confirming a paid purchase, review the displayed price, applicable taxes, access period and any purchase-specific cancellation or refund conditions. If an offering does not explain what you are buying, contact us before paying. Do not assume a one-time payment creates a recurring subscription.", "Payments are processed through Razorpay. A successful payment must be verified before it is treated as complete. Test-mode checkout uses simulated payments and does not represent a live charge.", "For a failed payment, duplicate charge, billing error or refund request, email info@pivotnow.in with your order reference. Never send a card number, CVV or payment password. Your rights under applicable consumer law are not limited by these terms."],
      },
      {
        id: "third-parties", title: "7. Connected services",
        paragraphs: ["Some features rely on market-data providers, AI providers, hosting services, payment processors or integrations you choose to connect. Their own terms and availability may apply. Connecting a broker or data account does not enable live order execution through Pivot."],
      },
      {
        id: "availability", title: "8. Changes, suspension and responsibility",
        paragraphs: ["We may update features, perform maintenance or restrict access to protect the service, investigate misuse or comply with law. Where practical, we will explain a material restriction or change. You may stop using Pivot at any time and contact us about account closure.", "To the extent permitted by law, Pivot Tech is not responsible for investment losses, lost opportunities or indirect losses arising from reliance on platform output. Nothing here excludes liability or rights that cannot legally be excluded.", "These terms are governed by applicable Indian law. We will update the date on this page when the terms change and give notice of material changes through the service or an available account contact. Questions or concerns can be sent to info@pivotnow.in."],
      },
    ],
  },
  privacy: {
    title: "Privacy policy",
    introduction: "This policy explains how Pivot Tech handles personal information when you visit pivotnow.in or use Pivot. It covers the information you provide, the activity the service records and the providers involved in running it.",
    sections: [
      {
        id: "information", title: "1. Information we handle",
        paragraphs: ["When you create an account, we handle your name, email and sign-in information. If you sign in through a provider such as Google, we receive the profile details supplied through that sign-in process.", "When you use the workspace, we store information such as conversations, prompts, saved layouts, chart drawings, alerts, notes, strategies and simulated portfolio or trade records. If you connect an integration, we process its authorisation tokens and the data available within the permissions you grant.", "The service may also record account identifiers, feature usage, request metadata, browser or device information and error logs. For checkout and support, we handle order references, payment status, verification details and the information you include when contacting us."],
      },
      {
        id: "purpose", title: "2. Why we use it",
        paragraphs: ["We use information to sign you in, save your work, answer your questions, run the features you request, verify payments and respond to support requests. We also use service activity to diagnose errors, understand which features work well, protect accounts and improve the platform.", "We may send account, security, payment and service notices. Where a separate permission is required for a use of your information, we will request it for that purpose."],
      },
      {
        id: "ai", title: "3. AI assistance",
        paragraphs: ["To answer a question, Pivot may send your prompt and relevant conversation or workspace context to the AI provider handling that feature. This can include information you supply about a portfolio or strategy. Only include information you are comfortable processing through the feature, and avoid passwords, payment credentials or unnecessary sensitive details.", "Processing by an AI provider is subject to that provider’s terms and data practices. Providers may have different rules for retaining data and using it to improve their models."],
      },
      {
        id: "providers", title: "4. Providers and sharing",
        paragraphs: ["Service providers process information needed for hosting, authentication, AI responses, analytics and payments. Razorpay processes checkout information under its own privacy policy. Pivot receives order and payment verification information; the standard checkout does not send your card number or CVV to Pivot’s application server.", "Where enabled, Pivot’s backend uses PostHog for product analytics and error reporting. Events can include an account identifier, actions such as signing in or running a backtest, and feature metadata. Your account email may be included in analytics profile properties. The current integration uses PostHog’s US ingestion endpoint, so this information may be processed outside India.", "Information may also be disclosed when required by applicable law, to respond to a valid legal request or to investigate security incidents. An integration you choose to use may process information under its own policy."],
      },
      {
        id: "device-storage", title: "5. Cookies and device storage",
        paragraphs: ["Pivot uses browser storage and, where needed, cookies to maintain sign-in sessions, remember preferences and preserve local workspace state. Checkout also uses a temporary cookie to associate a payment verification request with the order created for that browser.", "You can clear cookies and local storage in your browser. Doing so may sign you out or remove preferences and work stored only on that device. Blocking necessary storage can prevent some features from working."],
      },
      {
        id: "retention", title: "6. Retention and security",
        paragraphs: ["Saved account and workspace information is retained to provide the service. Some records may need to remain for payment reconciliation, security investigations or legal obligations. Retention can differ between the application, operational logs, backups and service providers; deleting a local copy does not automatically delete every server copy.", "We use access controls and other safeguards appropriate to the service. No online system is completely secure. Keep your sign-in details private and contact us if you suspect unauthorised access."],
      },
      {
        id: "choices", title: "7. Your choices and requests",
        paragraphs: ["Email info@pivotnow.in to request access to, correction of or deletion of your personal information, to close your account, to withdraw a permission or to raise a privacy concern. Please identify the account and the request without sending your password. We may need to verify that the account belongs to you before acting.", "We will handle requests in accordance with applicable law and explain any information we must retain or request we cannot fulfil. Withdrawing permission may affect features that depend on it. You can also disconnect an integration through its provider’s account controls.", "Pivot accounts are intended for adults aged 18 or over. If you believe a child has provided personal information, contact us so we can investigate."],
      },
      {
        id: "contact", title: "8. Contact and updates",
        paragraphs: ["Pivot Tech is responsible for the Pivot service described in this policy. For privacy questions or complaints, contact info@pivotnow.in.", "We will update the date on this page when our practices or this policy change. For material changes, we will provide notice through the service or an available account contact where appropriate."],
      },
    ],
  },
};
