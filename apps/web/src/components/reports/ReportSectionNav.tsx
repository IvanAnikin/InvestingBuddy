import type { TocHeading } from "./markdownUtils";

/**
 * Lightweight, sticky mini table of contents derived from the report's
 * level-2/3 headings. Purely a navigation aid — anchors scroll within the
 * already-rendered internal draft; no content is added or altered.
 */
export default function ReportSectionNav({
  headings,
}: {
  headings: TocHeading[];
}) {
  if (headings.length < 2) return null;

  return (
    <nav
      aria-label="Report sections"
      className="hidden max-h-[70vh] w-56 shrink-0 overflow-auto lg:block"
    >
      <div className="sticky top-24">
        <p className="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          On this page
        </p>
        <ul className="space-y-0.5 border-l border-white/10">
          {headings.map((h, i) => (
            <li key={`${h.id}-${i}`}>
              <a
                href={`#${h.id}`}
                className={`block border-l border-transparent py-1 text-xs text-slate-400 transition-colors hover:border-sky-400/60 hover:text-slate-100 ${
                  h.level === 3 ? "pl-6" : "pl-3"
                }`}
              >
                {h.text}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
