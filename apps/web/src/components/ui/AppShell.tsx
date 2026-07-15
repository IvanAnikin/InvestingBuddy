"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export interface NavLink {
  href: string;
  label: string;
}

/**
 * Modernized admin chrome: a fixed top compliance strip, a translucent glass
 * navigation bar with active-route highlighting, the page content area, and a
 * footer that restates the internal-only safety copy.
 *
 * Presentation only — the mandatory disclaimer wording is passed in verbatim so
 * the required internal-only / not-investment-advice text is preserved.
 */
export default function AppShell({
  navLinks,
  topDisclaimer,
  footer,
  children,
}: {
  navLinks: NavLink[];
  topDisclaimer: ReactNode;
  footer: ReactNode;
  children: ReactNode;
}) {
  const pathname = usePathname();

  function isActive(href: string): boolean {
    if (href === "/admin") return pathname === "/admin";
    return pathname === href || pathname.startsWith(href + "/");
  }

  return (
    <div className="relative flex min-h-screen flex-col">
      {/* Top compliance strip */}
      <div className="border-b border-rose-400/20 bg-rose-950/40 px-4 py-2 text-center text-[11px] font-medium uppercase tracking-wide text-rose-200 backdrop-blur-md">
        {topDisclaimer}
      </div>

      {/* Glass navigation */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-slate-950/50 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl items-center gap-5 px-4 py-3">
          <Link
            href="/"
            className="mr-1 text-sm text-slate-500 transition-colors hover:text-slate-200"
          >
            ← Home
          </Link>
          <Link href="/admin" className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-sky-500 to-violet-500 text-xs font-bold text-white shadow-lg shadow-sky-500/20">
              IB
            </span>
            <span className="text-sm font-semibold text-slate-100">
              InvestingBuddy Admin
            </span>
          </Link>
          <nav className="ml-3 flex flex-wrap gap-1">
            {navLinks.map((l) => {
              const active = isActive(l.href);
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                    active
                      ? "bg-white/10 text-white"
                      : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
                  }`}
                >
                  {l.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

      {/* Page content */}
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        {children}
      </main>

      {/* Footer */}
      <footer className="border-t border-white/10 bg-slate-950/40 px-4 py-5 text-center text-[11px] text-slate-500 backdrop-blur-md">
        {footer}
      </footer>
    </div>
  );
}
