"use client";

import type { ReactElement, ReactNode } from "react";
import Link from "next/link";

type AuthShellProps = {
  eyebrow: string;
  title: string;
  subtitle: string;
  footerPrefix: string;
  footerHref: string;
  footerLinkLabel: string;
  children: ReactNode;
};

const PRODUCT_PILLARS = [
  {
    label: "Research",
    copy: "Live market context, structured data, and conversation-first workflows.",
  },
  {
    label: "Execution",
    copy: "Paper trading, watchlists, and automations in the same workspace.",
  },
  {
    label: "Discipline",
    copy: "A focused interface designed to reduce noise and keep decisions clear.",
  },
];

export function AuthShell({
  eyebrow,
  title,
  subtitle,
  footerPrefix,
  footerHref,
  footerLinkLabel,
  children,
}: AuthShellProps): ReactElement {
  return (
    <div
      className="min-h-screen overflow-y-auto"
      style={{
        background:
          "linear-gradient(180deg, #f7f7f4 0%, #f4f4f1 100%)",
      }}
    >
      <div
        className="mx-auto flex min-h-screen w-full max-w-6xl items-center px-6 py-10 sm:px-8 lg:px-10"
      >
        <div className="grid w-full gap-12 lg:grid-cols-[minmax(320px,1fr)_440px] lg:gap-20">
          <section className="flex flex-col justify-center lg:pr-8">
            <div
              className="inline-flex w-fit items-center gap-3 rounded-full border px-3 py-1.5 text-[11px] font-semibold uppercase tracking-[0.18em]"
              style={{
                borderColor: "rgba(15,18,22,0.08)",
                color: "var(--text-secondary)",
                background: "rgba(255,255,255,0.56)",
              }}
            >
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--text-primary)" }}
              />
              Investor workspace
            </div>

            <div className="mt-8">
              <div
                style={{
                  fontFamily: "var(--font-experiment)",
                  fontWeight: 600,
                  fontSize: 34,
                  lineHeight: 1,
                  letterSpacing: "-0.04em",
                  color: "var(--text-primary)",
                }}
              >
                pivot
              </div>
              <h2
                className="mt-8 max-w-xl text-4xl font-semibold tracking-tight sm:text-[3.25rem]"
                style={{ color: "var(--text-primary)", letterSpacing: "-0.05em", lineHeight: 1.02 }}
              >
                Professional investing infrastructure, without the clutter.
              </h2>
              <p
                className="mt-5 max-w-lg text-[15px] leading-7"
                style={{ color: "var(--text-secondary)" }}
              >
                Pivot brings research, market context, workflows, and paper execution into one calm interface built for repeatable decision-making.
              </p>
            </div>

            <div className="mt-10 max-w-xl divide-y" style={{ borderColor: "rgba(15,18,22,0.08)" }}>
              {PRODUCT_PILLARS.map((item) => (
                <div key={item.label} className="grid gap-2 py-4 sm:grid-cols-[110px_1fr] sm:gap-6">
                  <div
                    className="text-xs font-semibold uppercase tracking-[0.18em]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    {item.label}
                  </div>
                  <p className="text-sm leading-6" style={{ color: "var(--text-primary)" }}>
                    {item.copy}
                  </p>
                </div>
              ))}
            </div>

            <p className="mt-10 text-xs" style={{ color: "var(--text-tertiary)" }}>
              Data and analysis only. Not financial advice.
            </p>
          </section>

          <section className="flex items-center lg:justify-end">
            <div
              className="w-full rounded-[24px] border px-6 py-7 shadow-[0_18px_45px_rgba(15,18,22,0.06)] sm:px-8 sm:py-8"
              style={{
                background: "#ffffff",
                borderColor: "rgba(15,18,22,0.08)",
              }}
            >
              <div className="lg:hidden">
                <div
                  style={{
                    fontFamily: "var(--font-experiment)",
                    fontWeight: 600,
                    fontSize: 30,
                    lineHeight: 1,
                    letterSpacing: "-0.04em",
                    color: "var(--text-primary)",
                  }}
                >
                  pivot
                </div>
                <div className="mt-5 h-px w-full" style={{ background: "rgba(15,18,22,0.08)" }} />
              </div>

              <div className="mt-0 lg:mt-0">
                <div
                  className="text-[11px] font-semibold uppercase tracking-[0.18em]"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  {eyebrow}
                </div>
                <h1
                  className="mt-3 text-[2rem] font-semibold tracking-tight"
                  style={{ color: "var(--text-primary)", letterSpacing: "-0.045em" }}
                >
                  {title}
                </h1>
                <p className="mt-2 text-sm leading-6" style={{ color: "var(--text-secondary)" }}>
                  {subtitle}
                </p>
              </div>

              <div className="mt-8">{children}</div>

              <p className="mt-8 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
                {footerPrefix}{" "}
                <Link
                  href={footerHref}
                  className="font-semibold underline-offset-4 hover:underline"
                  style={{ color: "var(--text-primary)" }}
                >
                  {footerLinkLabel}
                </Link>
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
