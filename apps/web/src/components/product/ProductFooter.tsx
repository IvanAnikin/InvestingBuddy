import Link from "next/link";

/**
 * The single place the product surfaces state their standing position.
 *
 * The admin chrome repeats its compliance strip on every page because an
 * operator is expected to read it every time. A research reader is not, and
 * repeating the same paragraph after every section makes it invisible. So the
 * meaning is stated once, in full, here — and carried in the interface by the
 * compact research-status treatment rather than by more prose.
 */
export default function ProductFooter({
  /** True when the viewer is an allowlisted admin. Gates the diagnostics entry
      so an anonymous visitor is never pointed at an operational surface they
      cannot reach. */
  admin = false,
}: {
  admin?: boolean;
}) {
  return (
    <footer className="mt-24 border-t border-[color:var(--ib-line)]">
      <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
        <div className="flex flex-col gap-8 sm:flex-row sm:justify-between">
          <div className="max-w-md">
            <p className="text-sm font-semibold tracking-tight text-[color:var(--ib-ink)]">
              InvestingBuddy
            </p>
            <p className="mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
              An evidence-first research workspace. It collects primary
              documents, extracts and labels financial facts, and examines a
              thesis from several perspectives — so a human can review the
              evidence and decide.
            </p>
          </div>

          <nav aria-label="Footer" className="flex gap-12 text-sm">
            <div>
              <p className="mb-2.5 text-xs font-medium uppercase tracking-wider text-[color:var(--ib-ink-3)]">
                Research
              </p>
              <ul className="space-y-2">
                <li>
                  <Link
                    href="/research/company"
                    className="text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]"
                  >
                    Analyze a company
                  </Link>
                </li>
                <li>
                  <Link
                    href="/research/discover"
                    className="text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]"
                  >
                    Discovery
                  </Link>
                </li>
                <li>
                  <Link
                    href="/research/reports"
                    className="text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]"
                  >
                    Research library
                  </Link>
                </li>
              </ul>
            </div>
            <div>
              <p className="mb-2.5 text-xs font-medium uppercase tracking-wider text-[color:var(--ib-ink-3)]">
                Product
              </p>
              <ul className="space-y-2">
                <li>
                  <Link
                    href="/#how-it-works"
                    className="text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]"
                  >
                    How it works
                  </Link>
                </li>
                <li>
                  <Link
                    href="/#use-cases"
                    className="text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]"
                  >
                    Use cases
                  </Link>
                </li>
                {admin && (
                  <li>
                    <Link
                      href="/admin"
                      className="text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]"
                    >
                      Admin &amp; diagnostics
                    </Link>
                  </li>
                )}
              </ul>
            </div>
          </nav>
        </div>

        <p className="mt-10 border-t border-[color:var(--ib-line)] pt-6 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          InvestingBuddy is a research workspace, not an advisory service. Its
          output is internal research material that requires human review before
          it is used or relied on. It is not investment advice, it produces no
          ratings, price targets or return projections, and no report is
          published publicly. Financial figures are attributed to the document
          they came from; anything the system could not source is shown as
          missing rather than filled in.
        </p>
      </div>
    </footer>
  );
}
