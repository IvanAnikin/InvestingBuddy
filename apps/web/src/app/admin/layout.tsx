import type { ReactNode } from "react";
import AppShell, { type NavLink } from "@/components/ui/AppShell";

export const metadata = {
  title: "Admin — InvestingBuddy",
};

const navLinks: NavLink[] = [
  { href: "/admin", label: "Dashboard" },
  { href: "/admin/companies/new", label: "Add Company" },
  { href: "/admin/analysis", label: "Run Analysis" },
  { href: "/admin/discovery", label: "Discovery" },
  { href: "/admin/reports", label: "Draft Reports" },
  { href: "/admin/backtesting", label: "Backtesting" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell
      navLinks={navLinks}
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
