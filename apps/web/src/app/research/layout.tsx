import type { ReactNode } from "react";
import ProductFooter from "@/components/product/ProductFooter";
import ProductNav from "@/components/product/ProductNav";
import SkipLink from "@/components/product/SkipLink";
import { getServerSession } from "@/lib/auth/server";

export const metadata = {
  title: "Research — InvestingBuddy",
};

// The Proxy (src/proxy.ts) guarantees an authenticated, allowlisted admin has
// reached any /research route — the same gate that protects /admin. The session
// is read here only to render identity-dependent navigation.
export const dynamic = "force-dynamic";

const NAV = [
  { href: "/research", label: "Research" },
  { href: "/research/company", label: "Analyze" },
  { href: "/research/discover", label: "Discovery" },
  { href: "/research/reports", label: "Reports" },
];

/**
 * The research workspace shell.
 *
 * Deliberately lighter than the admin chrome: no fixed compliance strip, no
 * repeated banner. The equivalent meaning is carried by the compact research
 * status treatment on each surface and stated in full in the footer, once.
 * The admin chrome is unchanged and still says everything it said before.
 */
export default async function ResearchLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await getServerSession();

  return (
    <div data-ib-surface="product" className="flex min-h-screen flex-col">
      <SkipLink />
      <ProductNav
        items={NAV}
        admin={Boolean(session?.allowed)}
        signedIn={Boolean(session)}
      />
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-5 py-10 sm:px-8">
        {children}
      </main>
      <ProductFooter admin={Boolean(session?.allowed)} />
    </div>
  );
}
