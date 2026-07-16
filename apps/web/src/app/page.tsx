import Link from "next/link";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import StatusPill from "@/components/ui/StatusPill";

// Phase 22.3.1 — Web Deploy Cache Hardening.
// Render the homepage per-request instead of statically prerendering it. Under
// WEBSITE_RUN_FROM_PACKAGE with alwaysOn=false, a prerendered `/` could keep
// serving the previous build until a manual `az webapp restart`. Rendering
// dynamically means `/` always reflects the currently-mounted bundle (and the
// embedded x-ib-build-commit meta), removing the stale-homepage class of bug.
export const dynamic = "force-dynamic";

const STEPS = [
  {
    title: "Research",
    body: "Research agents gather financial data, filings, and news from cited sources.",
  },
  {
    title: "Debate",
    body: "Analysis agents debate the bull case, bear case, and valuation readiness.",
  },
  {
    title: "Validate",
    body: "Validation agents check every factual claim against its source.",
  },
  {
    title: "Human review",
    body: "A human admin reviews and approves every draft before anything is published.",
  },
];

export default function HomePage() {
  return (
    <main className="relative">
      <div className="mx-auto max-w-5xl px-6 py-20">
        {/* Hero */}
        <section className="ib-fade-up text-center">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300 backdrop-blur-md">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sky-400" />
            AI-driven, council-of-agents investment research
          </div>
          <h1 className="bg-gradient-to-b from-white to-slate-400 bg-clip-text text-5xl font-bold tracking-tight text-transparent sm:text-6xl">
            InvestingBuddy
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-slate-400">
            An AI research platform that uses a council of specialized agents to
            produce evidence-based, citation-backed investment research for
            medium-term opportunities in European public markets.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/admin"
              className="rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/25 transition-all hover:-translate-y-0.5 hover:shadow-sky-500/40"
            >
              Open Internal Admin Workspace →
            </Link>
            <a
              href="#how-it-works"
              className="rounded-lg border border-white/15 bg-white/5 px-5 py-2.5 text-sm font-semibold text-slate-100 backdrop-blur-md transition-colors hover:bg-white/10"
            >
              How it works
            </a>
          </div>
        </section>

        {/* Status banner */}
        <div className="mt-14">
          <SafetyBanner
            variant="warning"
            title="Phase 22.3 — Internal development (no public reports yet)"
          >
            <p>
              The platform is under active development. The internal admin
              workspace is live with a company analysis runner, final report
              generator, markdown report preview, and backtesting/judge
              workflow. All outputs remain internal drafts and require human
              review before any publication.{" "}
              <strong className="text-amber-100">
                No public reports are published yet.
              </strong>{" "}
              Nothing here is investment advice.
            </p>
          </SafetyBanner>
        </div>

        {/* How it works */}
        <section id="how-it-works" className="mt-16 scroll-mt-24">
          <h2 className="mb-2 text-2xl font-semibold text-white">
            How It Works
          </h2>
          <p className="mb-6 max-w-2xl text-sm text-slate-400">
            Every claim is backed by a source with a retrieval timestamp, and
            every draft is reviewed by a human analyst before it can be
            published.
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, i) => (
              <GlassCard key={step.title} hover className="p-5">
                <div className="mb-3 grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-sky-500/80 to-violet-500/80 text-sm font-bold text-white shadow-lg shadow-sky-500/20">
                  {i + 1}
                </div>
                <p className="text-sm font-semibold text-slate-100">
                  {step.title}
                </p>
                <p className="mt-1 text-xs leading-relaxed text-slate-400">
                  {step.body}
                </p>
              </GlassCard>
            ))}
          </div>
        </section>

        {/* About + focus */}
        <section className="mt-16 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <GlassCard className="p-6">
            <h2 className="mb-3 text-xl font-semibold text-white">About</h2>
            <p className="text-sm leading-relaxed text-slate-400">
              InvestingBuddy generates research the way an investment committee
              would: specialized agents research, debate, and validate an
              opportunity, and a human analyst signs off before publication.
              Every recommendation-grade output includes risks, catalysts, a
              confidence score, and citations.
            </p>
            <p className="mt-3 text-sm leading-relaxed text-slate-400">
              It focuses on medium-term horizons of 6 months to 3 years and
              targets under-researched small and mid-cap companies in real
              assets, energy transition, industrial, and defense sectors.
            </p>
          </GlassCard>
          <GlassCard className="p-6">
            <h2 className="mb-3 text-xl font-semibold text-white">
              Coming Soon
            </h2>
            <ul className="space-y-2 text-sm text-slate-400">
              <li>· Weekly investment research reports</li>
              <li>· Company deep-dive analysis with full citations</li>
              <li>· Watchlist monitoring and thesis tracking</li>
              <li>· Admin review and publication workflow</li>
              <li>· Backtesting and recommendation performance tracking</li>
              <li>· Personalized investor insights (Version 2)</li>
            </ul>
            <div className="mt-4 flex flex-wrap gap-2">
              <StatusPill label="Human-reviewed" color="green" />
              <StatusPill label="Citation-backed" color="cyan" />
              <StatusPill label="Not investment advice" color="amber" />
            </div>
          </GlassCard>
        </section>

        {/* Footer */}
        <footer className="mt-16 border-t border-white/10 pt-6 text-center text-xs text-slate-500">
          InvestingBuddy · Internal research platform · All outputs require human
          review · Not investment advice · No public reports are published yet.
        </footer>
      </div>
    </main>
  );
}
