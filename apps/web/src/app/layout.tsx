import type { Metadata } from "next";
import "./globals.css";
import AnimatedBackground from "@/components/ui/AnimatedBackground";
import { getBuildInfo } from "@/lib/build-info";

// Embed the build identifiers into every page's <head> as meta tags. This lets
// the deploy smoke check detect a stale prerendered homepage — if `/` serves an
// old build, its `x-ib-build-commit` will not match the deployed GitHub SHA.
// Only public build metadata is exposed here (no secrets); see lib/build-info.ts.
const build = getBuildInfo();

export const metadata: Metadata = {
  title: "InvestingBuddy — AI Investment Research",
  description:
    "AI-powered multi-agent investment research platform focused on medium-term opportunities in European public markets.",
  other: {
    "x-ib-build-commit": build.commit_sha,
    "x-ib-build-id": build.build_id,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="relative min-h-full bg-[#060913] text-slate-100">
        <AnimatedBackground />
        {children}
      </body>
    </html>
  );
}
