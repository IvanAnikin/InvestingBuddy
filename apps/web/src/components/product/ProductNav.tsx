"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

export interface ProductNavItem {
  href: string;
  label: string;
  /** Anchor links only highlight on the page that owns them. */
  anchor?: boolean;
}

/**
 * Primary product navigation for the landing page and the research workspace.
 *
 * Only the routes a person actually navigates by are listed. The operational
 * surface (`/admin/*`) is reachable from here as a single "Admin" entry, and
 * only for a signed-in, allowlisted admin — every other admin route stays
 * discoverable from inside the admin chrome rather than being hoisted into the
 * product navigation.
 */
export default function ProductNav({
  items,
  admin = false,
  signedIn = false,
}: {
  items: ProductNavItem[];
  /** True when the viewer is an allowlisted admin (adds the Admin entry). */
  admin?: boolean;
  /** True when a session exists (swaps "Sign in" for "Sign out"). */
  signedIn?: boolean;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  function isActive(item: ProductNavItem): boolean {
    if (item.anchor) return false;
    if (item.href === "/") return pathname === "/";
    return pathname === item.href || pathname.startsWith(item.href + "/");
  }

  const links = [
    ...items,
    ...(admin ? [{ href: "/admin", label: "Admin" }] : []),
  ];

  return (
    <header className="sticky top-0 z-40 border-b border-[color:var(--ib-line)] bg-[#060913]/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center gap-4 px-5 py-3.5 sm:px-8">
        <Link
          href="/"
          className="flex items-center gap-2.5"
          aria-label="InvestingBuddy home"
        >
          <span
            aria-hidden="true"
            className="grid h-7 w-7 place-items-center rounded-md border border-[color:var(--ib-line-strong)] text-[11px] font-semibold tracking-tight text-[color:var(--ib-ink)]"
          >
            IB
          </span>
          <span className="text-sm font-semibold tracking-tight text-[color:var(--ib-ink)]">
            InvestingBuddy
          </span>
        </Link>

        <nav
          aria-label="Primary"
          className="ml-4 hidden items-center gap-1 md:flex"
        >
          {links.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item) ? "page" : undefined}
              className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
                isActive(item)
                  ? "text-[color:var(--ib-ink)]"
                  : "text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <Link
            href="/research/company"
            className="hidden rounded-md border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm font-medium text-[color:var(--ib-ink)] transition-colors hover:bg-[color:var(--ib-surface-raised)] sm:inline-block"
          >
            Analyze a company
          </Link>
          {signedIn ? (
            <form method="POST" action="/api/auth/signout">
              <button
                type="submit"
                className="rounded-md px-2.5 py-1.5 text-sm text-[color:var(--ib-ink-3)] transition-colors hover:text-[color:var(--ib-ink-2)]"
              >
                Sign out
              </button>
            </form>
          ) : (
            <Link
              href="/login"
              className="rounded-md px-2.5 py-1.5 text-sm text-[color:var(--ib-ink-3)] transition-colors hover:text-[color:var(--ib-ink-2)]"
            >
              Sign in
            </Link>
          )}

          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls="ib-mobile-nav"
            className="rounded-md border border-[color:var(--ib-line)] px-2.5 py-1.5 text-sm text-[color:var(--ib-ink-2)] md:hidden"
          >
            {open ? "Close" : "Menu"}
          </button>
        </div>
      </div>

      {open && (
        <nav
          id="ib-mobile-nav"
          aria-label="Primary (mobile)"
          className="border-t border-[color:var(--ib-line)] px-5 py-3 md:hidden"
        >
          <ul className="flex flex-col">
            {links.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={isActive(item) ? "page" : undefined}
                  onClick={() => setOpen(false)}
                  className="block border-b border-[color:var(--ib-line)] py-3 text-sm text-[color:var(--ib-ink-2)] last:border-0"
                >
                  {item.label}
                </Link>
              </li>
            ))}
            <li>
              <Link
                href="/research/company"
                onClick={() => setOpen(false)}
                className="mt-3 block rounded-md border border-[color:var(--ib-line-strong)] px-3 py-2.5 text-center text-sm font-medium text-[color:var(--ib-ink)]"
              >
                Analyze a company
              </Link>
            </li>
          </ul>
        </nav>
      )}
    </header>
  );
}
