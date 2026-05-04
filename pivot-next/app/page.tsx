"use client";

import { AppBootstrap } from "@/components/AppBootstrap";
import { AppShell } from "@/components/AppShell";

export default function HomePage(): React.ReactElement {
  return (
    <AppBootstrap>
      <AppShell />
    </AppBootstrap>
  );
}
