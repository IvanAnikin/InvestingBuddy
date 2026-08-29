import Link from "next/link";
import DiscoveryWorkbench from "./DiscoveryWorkbench";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Discovery — InvestingBuddy",
};

export default function DiscoverPage() {
  return (
    <div className="ib-fade-up">
      <nav aria-label="Breadcrumb" className="mb-6">
        <Link
          href="/research"
          className="text-sm text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
        >
          ← Research
        </Link>
      </nav>

      <header className="max-w-2xl">
        <h1 className="text-3xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Discover opportunities
        </h1>
        <p className="mt-3 text-base leading-relaxed text-[color:var(--ib-ink-2)]">
          Describe what you are looking for. Discovery turns it into a bounded
          universe, screens each company against the evidence it can find, and
          returns the candidates worth a full deep dive — with the reason each
          one surfaced.
        </p>
      </header>

      <div className="mt-8">
        <DiscoveryWorkbench />
      </div>

      <p className="mt-12 border-t border-[color:var(--ib-line)] pt-5 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Discovery prioritises research, not investments. Ticker-list runs, the
        discovery council review, the deep field review and the raw per-ticker
        warning stream remain on the{" "}
        <Link
          href="/admin/discovery"
          className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          admin discovery workspace
        </Link>
        .
      </p>
    </div>
  );
}
