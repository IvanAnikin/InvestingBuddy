import type { Metadata } from "next";
import "./globals.css";

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
      <body className="min-h-full flex flex-col bg-white text-gray-900">
        {children}
      </body>
    </html>
  );
}
