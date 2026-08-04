"use client";

import { AppShell } from "@/components/AppShell";

// AppBootstrap (auth gate + token provider) is wired once in
// app/layout.tsx, so each page just renders its own shell content.
export default function HomePage(): React.ReactElement {
  return <AppShell />;
}
