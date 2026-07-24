import type { ReactNode } from "react";
import AppShell, { type NavLink } from "@/components/ui/AppShell";
import { getServerSession } from "@/lib/auth/server";

export const metadata = {
  title: "Admin — InvestingBuddy",
};

// The Proxy (src/proxy.ts) guarantees an authenticated, allowlisted admin has
// reached any /admin route. We read the session here only to surface the
// signed-in identity + sign-out control in the shell.
export const dynamic = "force-dynamic";

const navLinks: NavLink[] = [
  { href: "/admin", label: "Dashboard" },
  { href: "/admin/companies/new", label: "Add Company" },
  { href: "/admin/analysis", label: "Run Analysis" },
  { href: "/admin/discovery", label: "Discovery" },
  { href: "/admin/reports", label: "Draft Reports" },
  { href: "/admin/backtesting", label: "Backtesting" },
  { href: "/admin/sources", label: "Sources" },
];

export default async function AdminLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await getServerSession();
  return (
    <AppShell
      navLinks={navLinks}
      user={
        session
          ? { email: session.email, name: session.name }
          : undefined
      }
      topDisclaimer={
        <>
          INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE — NOT FOR PUBLICATION —
          HUMAN REVIEW REQUIRED BEFORE ANY USE
        </>
      }
      footer={
        <>
          InvestingBuddy Admin Dashboard · Internal draft review and backtesting
          only · No BUY/SELL/HOLD recommendations · No price targets · All
          outputs require human review · Not investment advice
        </>
      }
    >
      {children}
    </AppShell>
  );
}
