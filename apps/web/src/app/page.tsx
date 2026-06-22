export default function HomePage() {
  return (
    <main className="flex-1">
      <div className="max-w-3xl mx-auto px-6 py-16">
        {/* Header */}
        <header className="mb-14">
          <h1 className="text-4xl font-bold tracking-tight text-gray-900 mb-3">
            InvestingBuddy
          </h1>
          <p className="text-lg text-gray-500">
            AI-powered investment research for medium-term opportunities in
            European public markets.
          </p>
        </header>

        {/* About */}
        <section className="mb-12">
          <h2 className="text-xl font-semibold text-gray-800 mb-3">About</h2>
          <p className="text-gray-600 leading-relaxed">
            InvestingBuddy is an AI-driven research platform that uses a
            council-of-agents approach to generate evidence-based investment
            research. Specialized agents research, debate, validate, and publish
            investment opportunities — every claim backed by citations, every
            recommendation reviewed by a human analyst before publication.
          </p>
          <p className="text-gray-600 leading-relaxed mt-3">
            The platform focuses on medium-term horizons of 6 months to 3 years
            and targets under-researched small and mid-cap companies in the real
            assets, energy transition, industrial, and defense sectors.
          </p>
        </section>

        {/* Investment approach */}
        <section className="mb-12">
          <h2 className="text-xl font-semibold text-gray-800 mb-3">
            How It Works
          </h2>
          <ol className="space-y-2 text-gray-600 list-decimal list-inside">
            <li>Research agents gather financial data, filings, and news</li>
            <li>Analysis agents debate bull case, bear case, and valuation</li>
            <li>Validation agents check every factual claim against sources</li>
            <li>Human admin reviews and approves before publication</li>
            <li>Published reports are tracked for backtesting and learning</li>
          </ol>
        </section>

        {/* Status */}
        <section className="mb-12">
          <h2 className="text-xl font-semibold text-gray-800 mb-3">
            Platform Status
          </h2>
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
            <p className="font-medium text-amber-800">
              Phase 4 in progress — Financial Data Provider Foundation
            </p>
            <p className="mt-1 text-sm text-amber-700">
              The platform is under active development. Real reports, live
              financial-data integrations, and agent-generated research are not
              yet available to users.
            </p>
          </div>
        </section>

        {/* Roadmap */}
        <section>
          <h2 className="text-xl font-semibold text-gray-800 mb-3">
            Coming Soon
          </h2>
          <ul className="space-y-1 text-gray-600">
            <li>Weekly investment research reports</li>
            <li>Company deep-dive analysis with full citations</li>
            <li>Watchlist monitoring and thesis tracking</li>
            <li>Admin review and publication workflow</li>
            <li>Backtesting and recommendation performance tracking</li>
            <li>Personalized investor insights (Version 2)</li>
          </ul>
        </section>
      </div>
    </main>
  );
}
