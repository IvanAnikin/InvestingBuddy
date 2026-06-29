import Link from "next/link";
import type { ReactNode } from "react";

export const metadata = {
  title: "Admin — InvestingBuddy",
};

const navLinks = [
  { href: "/admin", label: "Dashboard" },
  { href: "/admin/companies/new", label: "Add Company" },
  { href: "/admin/analysis", label: "Run Analysis" },
  { href: "/admin/reports", label: "Draft Reports" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      {/* Admin disclaimer banner */}
      <div className="bg-red-700 text-white text-center text-xs py-2 px-4 font-medium tracking-wide">
        INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE — NOT FOR PUBLICATION —
        HUMAN REVIEW REQUIRED BEFORE ANY USE
      </div>

      {/* Top nav */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-6">
          <Link
            href="/"
            className="text-sm text-gray-400 hover:text-gray-700 mr-2"
          >
            ← Home
          </Link>
          <span className="font-semibold text-gray-900 text-sm">
            InvestingBuddy Admin
          </span>
          <nav className="flex gap-4 ml-4">
            {navLinks.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="text-sm text-gray-600 hover:text-gray-900 hover:underline"
              >
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1 max-w-6xl mx-auto w-full px-4 py-8">
        {children}
      </main>

      {/* Footer disclaimer */}
      <footer className="border-t border-gray-200 bg-white text-center text-xs text-gray-400 py-4 px-4">
        InvestingBuddy Admin Dashboard · Phase 10 · Internal draft review only ·
        No BUY/SELL/HOLD recommendations · All outputs require human review
        before publication · Not investment advice
      </footer>
    </div>
  );
}
