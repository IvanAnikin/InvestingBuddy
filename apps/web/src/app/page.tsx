import Link from "next/link";
import ComparisonPanel from "@/components/product/ComparisonPanel";
import PrimaryCTA from "@/components/product/PrimaryCTA";
import ProductFooter from "@/components/product/ProductFooter";
import ProductNav from "@/components/product/ProductNav";
import ResearchFlow from "@/components/product/ResearchFlow";
import Reveal from "@/components/product/Reveal";
import SectionHeading from "@/components/product/SectionHeading";
import SkipLink from "@/components/product/SkipLink";
import UseCaseSelector from "@/components/product/UseCaseSelector";
import WorkflowStages from "@/components/product/WorkflowStages";
import { getServerSession } from "@/lib/auth/server";

// Phase 22.3.1 — Web Deploy Cache Hardening (retained).
// Render per-request instead of statically prerendering. Under
// WEBSITE_RUN_FROM_PACKAGE with alwaysOn=false, a prerendered `/` could keep
// serving the previous build until a manual `az webapp restart`. Rendering
// dynamically means `/` always reflects the currently-mounted bundle (and the
// embedded x-ib-build-commit meta), removing the stale-homepage class of bug.
// It is also required now that the nav reflects the viewer's session.
export const dynamic = "force-dynamic";

export const metadata = {
  title: "InvestingBuddy — Evidence-first investment research",
  description:
    "Discover companies, extract facts from their primary documents, challenge the thesis with multiple research agents, and turn scattered evidence into an auditable research report you review yourself.",
};

const NAV = [
  { href: "/research", label: "Research" },
  { href: "/research/discover", label: "Discovery" },
  { href: "/research/reports", label: "Reports" },
  { href: "/#how-it-works", label: "How it works", anchor: true },
  { href: "/#use-cases", label: "Use cases", anchor: true },
];

const COMPANY_STEPS = [
  "Company identity resolved from ticker and exchange",
  "Primary documents located on the issuer's own domains",
  "Latest annual and latest current-period reporting retrieved",
  "Financial facts extracted, with period, scope and currency",
  "Multi-year series reconstructed where the data supports it",
  "Regulated disclosures pulled from official venues",
  "Evidence pack assembled and cited",
  "Research agents analyse it from separate angles",
  "Red team argues against the emerging view",
  "Structured research report assembled",
  "You review the evidence and decide",
];

const DISCOVERY_EXAMPLES = [
  "European luxury companies",
  "Small-cap European industrial automation",
  "Nordic businesses exposed to data-centre investment",
  "European companies benefiting from grid modernisation",
];

const DISCOVERY_STEPS = [
  "Your description is parsed into theme, region, sector and market",
  "A bounded universe is generated from an auditable registry",
  "Candidates are screened and ranked, each with the reason it surfaced",
  "You pick the ones worth a full deep dive",
  "Full company research runs on the ones you choose",
  "Research packages stay comparable across candidates",
];

const PILLARS = [
  {
    title: "Primary evidence first",
    body: "Research starts from issuer documents and regulated disclosures, not from a model's recollection of a company.",
  },
  {
    title: "Period-aware financials",
    body: "Annual, half-year and quarterly figures are kept apart, and so are Group and segment. A part-year figure is never annualised to look comparable.",
  },
  {
    title: "Multi-agent challenge",
    body: "A thesis is examined by several agents with different jobs — including one whose job is to argue against it — rather than produced by a single assistant.",
  },
  {
    title: "Auditable by design",
    body: "Every figure keeps the document, page, period, scope and currency it came from, so a claim can be traced back to its source.",
  },
  {
    title: "Honest about gaps",
    body: "What could not be sourced stays visible as missing. Conflicting figures are surfaced rather than quietly reconciled.",
  },
  {
    title: "You keep the judgement",
    body: "The system does the research. It produces no rating, price target or return projection, and every report requires a human to review it.",
  },
];

const NOT_THIS = [
  "a market terminal",
  "a stock screener",
  "a transcript database",
  "a charting platform",
  "a general-purpose AI chat window",
];

export default async function LandingPage() {
  const session = await getServerSession();

  return (
    <div data-ib-surface="product" className="min-h-screen">
      <SkipLink />
      <ProductNav
        items={NAV}
        admin={Boolean(session?.allowed)}
        signedIn={Boolean(session)}
      />

      <main id="main" className="mx-auto max-w-6xl px-5 sm:px-8">
        {/* ---------------------------------------------------------------
            Hero
        --------------------------------------------------------------- */}
        <section className="grid items-center gap-10 py-16 sm:py-24 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-16">
          <div className="ib-fade-up">
            <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-[color:var(--ib-line)] px-3 py-1 text-xs text-[color:var(--ib-ink-3)]">
              <span
                aria-hidden="true"
                className="h-1.5 w-1.5 rounded-full bg-[color:var(--ib-accent)]"
              />
              Evidence-first research workspace
            </p>

            <h1 className="text-4xl font-semibold leading-[1.08] tracking-tight text-[color:var(--ib-ink)] sm:text-5xl lg:text-[3.4rem]">
              Evidence-first
              <br />
              investment research.
            </h1>

            <p className="mt-6 max-w-xl text-lg leading-relaxed text-[color:var(--ib-ink-2)]">
              Discover companies, extract the facts from their own primary
              documents, challenge the thesis with several research agents, and
              turn scattered evidence into an auditable research report — one
              you review yourself before it means anything.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <PrimaryCTA href="/research/company" testId="hero-cta-analyze">
                Analyze a company
              </PrimaryCTA>
              <PrimaryCTA
                href="/research/discover"
                tone="secondary"
                testId="hero-cta-discover"
              >
                Discover opportunities
              </PrimaryCTA>
              <Link
                href="#how-it-works"
                className="ib-arrow-host px-1 py-2.5 text-sm text-[color:var(--ib-ink-3)] transition-colors hover:text-[color:var(--ib-ink-2)]"
              >
                View the research workflow{" "}
                <span className="ib-arrow" aria-hidden="true">
                  ↓
                </span>
              </Link>
            </div>

            <p className="mt-8 max-w-xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
              No ratings, no price targets, no return projections. The system
              assembles and challenges evidence; the investment judgement stays
              with you.
            </p>
          </div>

          <div className="ib-fade-up lg:pl-4">
            <ResearchFlow />
          </div>
        </section>

        {/* ---------------------------------------------------------------
            Two primary workflows
        --------------------------------------------------------------- */}
        <Reveal as="section" className="scroll-mt-24 py-16 sm:py-20">
          <SectionHeading
            eyebrow="Two ways in"
            title="Start from a company, or start from an idea."
            lede="Both paths end in the same place: a cited, period-aware research report with its evidence attached."
          />

          <div className="mt-10 grid gap-4 lg:grid-cols-2">
            {/* Analyze a company */}
            <article className="ib-panel ib-panel-hover flex flex-col p-6 sm:p-8">
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                Path A
              </p>
              <h3 className="mt-2 text-xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
                Analyze a company
              </h3>
              <p className="mt-2 max-w-md text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                You already know which company you want to understand. Give it a
                ticker and the pipeline does the collecting, extracting and
                challenging.
              </p>

              <ol className="mt-6 flex-1 space-y-2.5">
                {COMPANY_STEPS.map((step, i) => (
                  <li
                    key={step}
                    className="flex gap-3 text-sm text-[color:var(--ib-ink-2)]"
                  >
                    <span className="mt-0.5 w-5 shrink-0 font-mono text-[10px] text-[color:var(--ib-ink-3)]">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>

              <div className="mt-7">
                <PrimaryCTA href="/research/company">
                  Analyze a company
                </PrimaryCTA>
              </div>
            </article>

            {/* Discover opportunities */}
            <article className="ib-panel ib-panel-hover flex flex-col p-6 sm:p-8">
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                Path B
              </p>
              <h3 className="mt-2 text-xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
                Discover opportunities
              </h3>
              <p className="mt-2 max-w-md text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                You have a theme, a market or a hunch. Describe it in your own
                words and the system builds a bounded universe to work through.
              </p>

              <ul className="mt-6 space-y-2">
                {DISCOVERY_EXAMPLES.map((example) => (
                  <li
                    key={example}
                    className="rounded-lg border border-[color:var(--ib-line)] px-3 py-2 font-mono text-xs text-[color:var(--ib-ink-2)]"
                  >
                    “{example}”
                  </li>
                ))}
              </ul>

              <ol className="mt-6 flex-1 space-y-2.5">
                {DISCOVERY_STEPS.map((step, i) => (
                  <li
                    key={step}
                    className="flex gap-3 text-sm text-[color:var(--ib-ink-2)]"
                  >
                    <span className="mt-0.5 w-5 shrink-0 font-mono text-[10px] text-[color:var(--ib-ink-3)]">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>

              <div className="mt-7">
                <PrimaryCTA href="/research/discover" tone="secondary">
                  Start discovery
                </PrimaryCTA>
              </div>
            </article>
          </div>
        </Reveal>

        {/* ---------------------------------------------------------------
            How it works
        --------------------------------------------------------------- */}
        <Reveal
          as="section"
          className="scroll-mt-24 border-t border-[color:var(--ib-line)] py-16 sm:py-20"
        >
          <div id="how-it-works" className="scroll-mt-24">
            <SectionHeading
              eyebrow="How it works"
              title="Seven stages, and you can see inside each one."
              lede="Research is a pipeline, not a prompt. Every stage records what it found, what it could not find, and where each figure came from."
            />
          </div>
          <div className="mt-10">
            <WorkflowStages />
          </div>
        </Reveal>

        {/* ---------------------------------------------------------------
            Why
        --------------------------------------------------------------- */}
        <Reveal
          as="section"
          className="border-t border-[color:var(--ib-line)] py-16 sm:py-20"
        >
          <SectionHeading
            eyebrow="Why it is built this way"
            title="Design decisions you can check, not claims you have to trust."
          />

          <div className="mt-10 grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
            {PILLARS.map((p) => (
              <div key={p.title}>
                <h3 className="text-sm font-semibold text-[color:var(--ib-ink)]">
                  {p.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
                  {p.body}
                </p>
              </div>
            ))}
          </div>
        </Reveal>

        {/* ---------------------------------------------------------------
            Use cases
        --------------------------------------------------------------- */}
        <Reveal
          as="section"
          className="scroll-mt-24 border-t border-[color:var(--ib-line)] py-16 sm:py-20"
        >
          <div id="use-cases" className="scroll-mt-24">
            <SectionHeading
              eyebrow="Who it is for"
              title="The same workflow, read from your seat."
            />
          </div>
          <div className="mt-10">
            <UseCaseSelector />
          </div>
        </Reveal>

        {/* ---------------------------------------------------------------
            Comparison
        --------------------------------------------------------------- */}
        <Reveal
          as="section"
          className="border-t border-[color:var(--ib-line)] py-16 sm:py-20"
        >
          <SectionHeading
            eyebrow="Against the manual workflow"
            title="The gathering still happens. It just stops disappearing."
          />
          <div className="mt-10">
            <ComparisonPanel />
          </div>
        </Reveal>

        {/* ---------------------------------------------------------------
            Positioning
        --------------------------------------------------------------- */}
        <Reveal
          as="section"
          className="border-t border-[color:var(--ib-line)] py-16 sm:py-20"
        >
          <div className="grid gap-10 lg:grid-cols-2">
            <SectionHeading
              eyebrow="What it is"
              title="A research workflow, not a data terminal."
              lede="InvestingBuddy orchestrates discovery, primary evidence, financial extraction, research agents and auditability into one pass. It sits before the decision, not at it."
            />
            <div className="ib-panel p-6 sm:p-7">
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                It is not
              </p>
              <ul className="mt-4 space-y-2.5">
                {NOT_THIS.map((item) => (
                  <li
                    key={item}
                    className="flex gap-2.5 text-sm text-[color:var(--ib-ink-2)]"
                  >
                    <span
                      aria-hidden="true"
                      className="mt-2 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                    />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-5 border-t border-[color:var(--ib-line)] pt-4 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
                It does not execute trades, connect to a broker, or manage a
                portfolio — and it never will as part of this product.
              </p>
            </div>
          </div>
        </Reveal>

        {/* ---------------------------------------------------------------
            Final CTA
        --------------------------------------------------------------- */}
        <Reveal
          as="section"
          className="border-t border-[color:var(--ib-line)] py-16 sm:py-24"
        >
          <div className="ib-panel px-6 py-10 text-center sm:px-10 sm:py-14">
            <h2 className="mx-auto max-w-2xl text-2xl font-semibold tracking-tight text-[color:var(--ib-ink)] sm:text-3xl">
              Start with one company, or with the idea behind it.
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-[color:var(--ib-ink-2)]">
              Research runs inside a private workspace. Sign in with your
              authorised account to begin.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <PrimaryCTA href="/research/company">
                Analyze a company
              </PrimaryCTA>
              <PrimaryCTA href="/research/discover" tone="secondary">
                Discover opportunities
              </PrimaryCTA>
              <PrimaryCTA href="/research/reports" tone="quiet">
                View existing research
              </PrimaryCTA>
            </div>
          </div>
        </Reveal>
      </main>

      <ProductFooter admin={Boolean(session?.allowed)} />
    </div>
  );
}
