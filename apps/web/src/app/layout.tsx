import type { Metadata } from "next";
import "./globals.css";
import AnimatedBackground from "@/components/ui/AnimatedBackground";

export const metadata: Metadata = {
  title: "InvestingBuddy — AI Investment Research",
  description:
    "AI-powered multi-agent investment research platform focused on medium-term opportunities in European public markets.",
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
