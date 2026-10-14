/* The company and legal pages: About, Terms, Privacy, Refunds, Contact.
 *
 * Every statement here describes how Pivot actually works today. When the
 * product changes (a new data provider, a new region, a new way to pay),
 * change this file in the same commit, bump UPDATED, and send the
 * policy_update email (charto/data/mail_templates.py) for material changes.
 *
 * Written against: the Digital Personal Data Protection Act 2023 and DPDP
 * Rules 2025; the IT Act 2000 and its Reasonable Security Practices (SPDI)
 * Rules 2011; the Consumer Protection (E-Commerce) Rules 2020; SEBI's
 * position on unregistered investment advice; Razorpay's website checks. */

export type CompanyPageKey = "about" | "terms" | "privacy" | "refunds" | "contact";

export interface CompanySection {
  id: string;
  title: string;
  paragraphs: string[];
  list?: string[];
}

export interface CompanyContent {
  title: string;
  introduction: string;
  sections: CompanySection[];
}

/* Who operates Pivot. LEGAL_NAME and ADDRESS must match the company's
 * registration documents; ADDRESS prints only once it is filled in. */
export const COMPANY_NAME = "Pivot Tech";
export const LEGAL_NAME = "Pivot Tech";
export const ADDRESS = "";
export const CONTACT_EMAIL = "contact@pivotnow.in";
export const GRIEVANCE_OFFICER = { name: "", title: "Grievance Officer", email: "contact@pivotnow.in" };
export const UPDATED = { iso: "2026-10-14", label: "14 October 2026" };

const ADVICE_LINE = "Pivot is not registered with SEBI as an investment adviser or research analyst, and it is not a stock broker. Nothing on Pivot is a recommendation to buy, sell or hold any security.";

export const COMPANY_PAGES: Record<CompanyPageKey, CompanyContent> = {
  about: {
    title: "About Pivot",
    introduction: "Pivot is a market research platform for traders and investors in India. You ask a question about a stock, an index or a commodity, and Pivot answers it with real market data, draws the answer on a live chart, and shows the evidence behind it.",
    sections: [
      {
        id: "why", title: "Why we built it",
        paragraphs: [
          "Most market tools give you either a chart or an opinion. A chart without context leaves you to do all the reading yourself; an opinion without evidence asks you to trust it. We wanted something in between: a chart that can explain itself, and answers you can check.",
          "So every level Pivot marks comes with the bars that made it, every pattern comes with how often that pattern has actually worked, and every number in an answer comes from the data, not from the model's memory.",
        ],
      },
      {
        id: "what", title: "What you can do with Pivot",
        paragraphs: ["Pivot covers NSE and BSE equities, the major indices, NSE futures and options, MCX commodities and leading crypto pairs. With it you can:"],
        list: [
          "Ask questions on a live chart and see the answer drawn as levels, trendlines, zones and notes.",
          "Read companies in depth: quarterly results, ratios, shareholding and facts taken from annual-report filings.",
          "Compare companies, screen the market and follow an option chain.",
          "Set price, indicator and multi-condition alerts that notify you in the app and by email.",
          "Publish a chart setup as a read-only link that others can copy into their own workspace.",
          "Test trading ideas against history and follow them in a simulated paper book. These tools are opening to accounts in stages.",
        ],
      },
      {
        id: "principles", title: "How we work",
        paragraphs: ["A few rules shape every part of the product:"],
        list: [
          "Evidence first. If the data to answer a question is not available, Pivot says so instead of guessing.",
          "Honest testing. A backtest reports its sample size and how it compares with chance, so a lucky result is not presented as an edge.",
          "Simulation, not execution. Pivot does not place orders with a broker. Paper trades are simulations and never move money.",
          "Your data stays yours. We do not sell personal data, and our privacy policy names every service that processes it.",
        ],
      },
      {
        id: "role", title: "What Pivot is not",
        paragraphs: [
          ADVICE_LINE,
          "Pivot gives you data, analysis and tools to form your own view. Markets carry risk, analysis can be wrong, and past or simulated results do not promise future ones. Please make your own decisions, and consult a SEBI-registered adviser for advice on your situation.",
        ],
      },
      {
        id: "company", title: "The company",
        paragraphs: [
          "Pivot is built and operated by Pivot Tech, an early-stage team in India. We ship often and listen closely. If something is unclear, missing or wrong, write to contact@pivotnow.in and a member of the team will reply.",
        ],
      },
    ],
  },

  terms: {
    title: "Terms of service",
    introduction: "These terms are an agreement between you and Pivot Tech (“Pivot”, “we”, “us”) for your use of pivotnow.in, app.pivotnow.in and the services linked to them (together, “Pivot”). Please read them carefully. By creating an account or using Pivot, you agree to them. If you do not agree, please do not use Pivot.",
    sections: [
      {
        id: "eligibility", title: "1. Your account",
        paragraphs: [
          "You must be at least 18 years old and able to enter a binding contract under Indian law to use Pivot. If you use Pivot on behalf of an organisation, you confirm you are authorised to accept these terms for it.",
          "Keep your account details accurate and your password private. One account is for one person; do not share it. You are responsible for activity under your account. Tell us at contact@pivotnow.in straight away if you think someone else has used it.",
        ],
      },
      {
        id: "service", title: "2. What Pivot provides",
        paragraphs: [
          "Pivot provides market data, charts, company research, an AI assistant, alerts, screening, shared chart setups, backtests and a simulated paper book. Features differ by plan and some are released to accounts in stages.",
          ADVICE_LINE,
          "Pivot's output is general information and analysis. It does not take into account your financial situation, objectives or risk tolerance, and it is not personalised investment advice. You are solely responsible for your investment and trading decisions.",
          "Pivot does not place live orders with any broker. Strategies, orders, balances and positions in the paper book are simulated: they do not buy or sell securities, and they do not create real profits or losses. Connecting a broker or data account does not enable live trading through Pivot.",
        ],
      },
      {
        id: "data", title: "3. Market data and AI have limits",
        paragraphs: [
          "Market data comes from third-party sources and may be delayed, incomplete or occasionally wrong. Company figures are taken from public filings and financial-data sources and may contain errors or be restated. The source of a price is shown where it is not the primary feed.",
          "Answers from the AI assistant can contain mistakes, even when they cite data. Check anything important against the underlying source before you rely on it.",
          "Backtests apply your rules to historical data under stated assumptions. They cannot fully reflect liquidity, slippage, costs, taxes or how you would actually have acted. A good backtest does not mean a strategy will work in future.",
          "Market and company data in Pivot comes from third parties and is for your personal use inside Pivot. You may not copy, scrape, resell or redistribute it.",
        ],
      },
      {
        id: "use", title: "4. Using Pivot responsibly",
        paragraphs: ["You agree not to:"],
        list: [
          "use Pivot for market manipulation, insider trading, fraud or any other unlawful purpose;",
          "present Pivot's output as investment advice or research to others, or use Pivot to provide paid tips, unless you are registered with SEBI to do so and comply with its rules;",
          "access another person's account, test or bypass our security, or disrupt the service;",
          "scrape, bulk-download or use automated tools against Pivot, or attempt to extract its source code, prompts or data;",
          "upload content that is unlawful, infringes someone's rights, or contains another person's personal data without their consent.",
        ],
      },
      {
        id: "content", title: "5. Your content and shared setups",
        paragraphs: [
          "You own what you create in Pivot: notes, drawings, layouts, alerts, strategies and conversations. You give us permission to store, process and display that content only as needed to run Pivot for you, as described in our Privacy Policy.",
          "When you publish a setup, anyone with its link can view it, and anyone who views it can copy it into their own workspace. Your display name stays on it as the original author. You can stop sharing at any time; copies already made remain with the people who made them. Do not include anything in a shared setup that you would not want made public.",
        ],
      },
      {
        id: "keys", title: "6. Your own AI keys",
        paragraphs: [
          "If you connect your own AI provider key or AI subscription, requests you make with it are sent to that provider and billed by it under its terms. We store your key encrypted and use it only for your requests. You are responsible for your arrangement with that provider.",
        ],
      },
      {
        id: "billing", title: "7. Plans, billing and renewals",
        paragraphs: [
          "Pivot has a Free plan and paid plans. Each plan's price, features and usage limits are shown on the pricing page before you pay. Prices are in Indian rupees and include GST.",
          "Paid plans are subscriptions that renew automatically each month or year through a payment mandate processed by Razorpay, until you cancel. You receive a notice before each renewal debit, as RBI rules for recurring payments require.",
          "You can cancel anytime from your billing settings. Cancelling stops future renewals; you keep your plan until the end of the period you have paid for. An upgrade starts straight away and is charged the difference for the current period; a downgrade takes effect at the end of the period.",
          "If a renewal payment fails, we retry and keep your plan for a short grace period. If it still cannot be collected, your account moves to the Free plan. Your saved work is not deleted.",
          "We may change plan prices with at least 30 days' notice by email. A change applies from your next renewal after the notice period, and you can cancel before it takes effect. Refunds are covered by our Refund and Cancellation Policy.",
        ],
      },
      {
        id: "ip", title: "8. Our software and brand",
        paragraphs: [
          "Pivot's software, design, text and brand belong to Pivot Tech or its licensors. Your plan gives you a personal, non-transferable right to use Pivot under these terms; it does not transfer any ownership. Company names and logos shown in Pivot belong to their owners and are used only to identify them.",
        ],
      },
      {
        id: "changes", title: "9. Changes and suspension",
        paragraphs: [
          "We improve Pivot continuously and may add, change or remove features. If we remove a paid feature that is central to your plan, we will tell you in advance and you may cancel and receive a proportionate refund for the unused period.",
          "We may suspend or close an account that breaches these terms, puts other users or the service at risk, or where the law requires it. Where it is safe and lawful, we will tell you why and give you a chance to respond. You may close your account at any time by writing to us.",
        ],
      },
      {
        id: "liability", title: "10. Responsibility and liability",
        paragraphs: [
          "Pivot is provided “as is” and “as available”. We work to keep it accurate and running, but we do not promise it will be uninterrupted, error-free or suitable for any particular purpose.",
          "To the extent the law allows, Pivot Tech is not liable for any trading or investment loss, lost profit or opportunity, or indirect or consequential loss arising from your use of Pivot or reliance on its output. Our total liability for any claim is limited to the amount you paid us in the 12 months before the claim.",
          "Nothing in these terms limits rights you have under the Consumer Protection Act, 2019, or any liability that cannot be limited by law.",
        ],
      },
      {
        id: "law", title: "11. Complaints, governing law and updates",
        paragraphs: [
          "If you have a complaint, write to our Grievance Officer at contact@pivotnow.in. We acknowledge complaints within 48 hours and aim to resolve them within one month.",
          "These terms are governed by the laws of India. Any dispute that we cannot resolve together is subject to the jurisdiction of the competent courts in India.",
          "We may update these terms. For material changes, we will email you at least 15 days before they take effect. If you keep using Pivot after that, the updated terms apply.",
        ],
      },
    ],
  },

  privacy: {
    title: "Privacy policy",
    introduction: "This policy explains what personal data Pivot Tech collects when you use Pivot, why, who processes it, how long we keep it, and the rights you have over it. Pivot Tech is the Data Fiduciary for this data under India's Digital Personal Data Protection Act, 2023. We do not sell your personal data.",
    sections: [
      {
        id: "collect", title: "1. The data we collect",
        paragraphs: ["We collect only what we need to run Pivot:"],
        list: [
          "Account: your name, email address and password. Passwords are stored only as a salted scrypt hash; we never store or see the password itself. If you sign in with Google, we receive your name and verified email address from Google.",
          "Your work: questions and conversations with the assistant, screenshots you attach, saved layouts, chart drawings, watchlists, alerts, notes, screens, strategies, backtests, paper trades and setups you publish.",
          "Voice input: if you use the microphone, the recording is sent for transcription and is not stored by us after the text is returned.",
          "Your own AI keys: if you connect an AI provider key or AI subscription, we store it encrypted (AES-256-GCM), never show it back in full, and send it only to that provider. For a ChatGPT sign-in we keep your display name, not your email.",
          "Payments: your plan, subscription and payment references and their status. Card, UPI and bank details are entered with Razorpay and never reach our servers.",
          "Waitlist: your email address and the experience level you choose.",
          "Usage and device data: which features you use, error reports, your browser and device type, and your IP address, which we use for security and abuse limits.",
          "Messages you send us: the content of emails to our support address.",
        ],
      },
      {
        id: "use", title: "2. Why we use it",
        paragraphs: ["We use your data for these purposes, and no others:"],
        list: [
          "to create and secure your account, sign you in and recover access;",
          "to provide the features you use: answering your questions, saving your work, running your alerts and sending their notifications;",
          "to process payments, issue invoices and meet tax and accounting obligations;",
          "to send account, security, billing and service emails, and product emails you can switch off;",
          "to understand how Pivot is used, fix errors and improve the product;",
          "to prevent fraud and abuse, and to comply with the law and lawful requests from authorities.",
        ],
      },
      {
        id: "basis", title: "3. Consent and your choices",
        paragraphs: [
          "We process your data on the basis of your consent, given when you create an account or use a feature, or for the legitimate uses the DPDP Act allows, such as data you share with us voluntarily for a specific purpose and compliance with law.",
          "You can withdraw consent at any time by writing to contact@pivotnow.in. Withdrawal does not affect processing that happened before it, but some features will stop working, and closing the account may be the only way to withdraw from all processing.",
        ],
      },
      {
        id: "ai", title: "4. How the AI assistant handles your data",
        paragraphs: [
          "When you ask a question, the question, the relevant part of the conversation and the context of the chart you are viewing are sent to the AI model that answers it. Pivot's assistant runs on Microsoft Azure's AI services, currently in Microsoft's United States data centres. Microsoft processes this data to provide the service and, under its terms, does not use it to train its models.",
          "When the assistant searches the web, the search terms go through our own search service to public search engines and to Brave Search; they are not linked to your name or email. Pages you open in the Browser widget may load through a browser running on our servers, so the site sees our address rather than yours. When you use your own AI key or subscription, your requests go to that provider and its own data terms apply.",
          "Please do not share passwords, card details, Aadhaar or PAN numbers, or other sensitive information in a conversation.",
        ],
      },
      {
        id: "share", title: "5. Who processes your data",
        paragraphs: ["We share personal data only with service providers that process it on our behalf, under contract, for the purposes above:"],
        list: [
          "Microsoft Azure: hosting, databases and backups (Central India region), the AI assistant and voice transcription (United States).",
          "Vercel: hosting of our website, pivotnow.in, and its waitlist form.",
          "Hostinger: our email service, including the emails we send you.",
          "Razorpay: payments and subscription mandates.",
          "PostHog: product analytics and error monitoring (United States).",
          "Google: sign-in, if you choose Sign in with Google, and YouTube searches when the assistant looks for videos.",
          "Brave Search: web searches the assistant runs for you.",
        ],
      },
      {
        id: "share-more", title: "6. Other disclosures",
        paragraphs: [
          "We may disclose personal data when the law requires it, to respond to a valid request from a court, regulator or law-enforcement agency, or to protect the rights and safety of our users and the public. If Pivot Tech is involved in a merger or acquisition, your data may transfer to the successor, which will be bound by this policy.",
          "Market-data and financial-data providers receive no personal data from us.",
        ],
      },
      {
        id: "where", title: "7. Where your data is stored",
        paragraphs: [
          "Your account and saved work are stored in India, in Microsoft Azure's Central India region. Some of the providers listed above process data outside India, mainly in the United States. We transfer data abroad only as the DPDP Act permits, and never to a country the Government of India has restricted.",
        ],
      },
      {
        id: "retention", title: "8. How long we keep it",
        list: [
          "Account and saved work: for as long as your account is open. When you close it, we delete it from our live systems within 30 days.",
          "Backups: copies of deleted data can remain in our encrypted backups. Backups are used only to recover from a failure and never to restore a closed account.",
          "Payment and invoice records: for 8 years, as Indian tax and accounting law requires.",
          "Security and server logs: up to 180 days.",
          "Email delivery records: 30 days. Password-reset links: they expire after 30 minutes and their records are deleted within a day.",
          "Waitlist entries: until you get access or ask us to remove you.",
        ],
        paragraphs: ["We keep personal data only as long as we need it for the purposes in this policy:"],
      },
      {
        id: "security", title: "9. How we protect it",
        paragraphs: [
          "Data travels over encrypted connections (TLS), passwords are hashed, AI keys are encrypted with a key that is not stored in the database, and backups are encrypted at rest. Access to production systems is limited to the people who need it.",
          "No system is perfectly secure. If a personal-data breach affects you, we will tell you without delay, explain what happened and what you can do, and report it to the Data Protection Board of India and CERT-In as the law requires.",
        ],
      },
      {
        id: "rights", title: "10. Your rights",
        paragraphs: ["Under the DPDP Act you have the right to:"],
        list: [
          "get a summary of the personal data we hold about you and how we process it;",
          "have inaccurate or incomplete data corrected, completed or updated;",
          "have your data erased when it is no longer needed, or close your account;",
          "withdraw consent;",
          "nominate someone to exercise these rights if you die or become unable to;",
          "have a grievance addressed by us, and then by the Data Protection Board of India if you are not satisfied.",
        ],
      },
      {
        id: "exercise", title: "11. How to exercise them",
        paragraphs: [
          "Email contact@pivotnow.in from the address on your account, saying what you would like us to do. We may need to confirm the request is yours before acting. We respond within 30 days, and tell you if there is a legal reason we must keep some data.",
          "You can also clear the data Pivot stores in your browser at any time.",
        ],
      },
      {
        id: "cookies", title: "12. Cookies and browser storage",
        paragraphs: [
          "Pivot uses your browser's local storage to keep you signed in and to remember your workspace and preferences, and a short-lived cookie during checkout. Our analytics service uses browser storage to tell visits apart; it creates a profile only once you sign in. We do not use advertising cookies and do not show third-party ads.",
          "Clearing your browser storage signs you out and removes preferences saved on that device.",
        ],
      },
      {
        id: "children", title: "13. Children",
        paragraphs: [
          "Pivot is for adults aged 18 and over. We do not knowingly collect data from children. If you believe a child has created an account, write to us and we will delete it.",
        ],
      },
      {
        id: "changes", title: "14. Changes to this policy",
        paragraphs: [
          "We will update this page when our practices change and show the date of the change at the top. For material changes, we will email you before they take effect.",
        ],
      },
    ],
  },

  refunds: {
    title: "Refund and cancellation policy",
    introduction: "This policy explains how to cancel a paid Pivot plan and when we refund a payment. It forms part of our Terms of Service.",
    sections: [
      {
        id: "cancel", title: "1. Cancelling your plan",
        paragraphs: [
          "You can cancel a paid plan at any time from your billing settings, or by writing to contact@pivotnow.in. Cancelling stops all future renewals and the payment mandate.",
          "You keep your plan's features until the end of the period you have already paid for. After that your account moves to the Free plan, and your saved work stays.",
        ],
      },
      {
        id: "refund", title: "2. When we refund",
        paragraphs: ["We refund the full amount when:"],
        list: [
          "you were charged twice for the same period, or charged in error;",
          "you were charged after you cancelled;",
          "a paid feature central to your plan was unavailable for a substantial part of the period because of a fault on our side.",
        ],
      },
      {
        id: "no-refund", title: "3. Partly used periods",
        paragraphs: [
          "Apart from the cases above, a period that has started is not refunded in part when you cancel, because you keep access until it ends. If you believe your situation deserves an exception, write to us; we look at every request.",
          "Nothing in this policy limits your rights under the Consumer Protection Act, 2019.",
        ],
      },
      {
        id: "how", title: "4. How to request a refund",
        paragraphs: [
          "Write to contact@pivotnow.in from the email on your account within 30 days of the payment, with the payment reference from your receipt. Never send card numbers, CVV, UPI PIN or OTPs.",
          "We reply within 2 working days. Approved refunds are issued to the original payment method within 7 working days, and banks usually take a further 5 to 7 working days to show them.",
        ],
      },
    ],
  },

  contact: {
    title: "Contact us",
    introduction: "We read every message and reply personally. The quickest way to reach us is email.",
    sections: [
      {
        id: "email", title: "Email",
        paragraphs: [
          "For support, billing, privacy requests, press and partnerships: contact@pivotnow.in. We reply within 2 working days.",
          "Please never send your password, card details, UPI PIN or OTPs. We will never ask for them.",
        ],
      },
      {
        id: "grievance", title: "Grievance Officer",
        paragraphs: [
          "For complaints about the service, billing or how we handle your personal data, write to our Grievance Officer at contact@pivotnow.in with the subject “Grievance”. We acknowledge every complaint within 48 hours and aim to resolve it within one month.",
          "If you are not satisfied with our response to a personal-data complaint, you may approach the Data Protection Board of India.",
        ],
      },
      {
        id: "security", title: "Reporting a security issue",
        paragraphs: [
          "If you have found a security vulnerability, write to contact@pivotnow.in with the subject “Security”. Please give us reasonable time to fix it before disclosing it, and do not access other people's data while testing.",
        ],
      },
    ],
  },
};
