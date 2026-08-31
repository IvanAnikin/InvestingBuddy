import Surface from "@/components/product/Surface";
import type { OpenQuestion } from "@/components/research/reportSections";

/**
 * What this research has not settled.
 *
 * This replaces "Important missing information — 24 items", a list of machine
 * field paths under a heading that implied they were what a reader was missing.
 * They were not: `identity.isin` is a gap in the record, and "is the current
 * growth rate durable?" is a gap in the understanding. Only the second belongs
 * here; the first is reported under research confidence.
 *
 * Every question below was raised by the chair, the red team, or a named
 * council agent. None is generated here.
 */
export default function OpenQuestions({
  questions,
}: {
  questions: OpenQuestion[];
}) {
  if (questions.length === 0) return null;
  const primary = questions.slice(0, 6);
  const rest = questions.slice(6);

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="open-questions"
      id="questions"
    >
      <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        Open research questions
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
        Raised by the council and not resolved by the evidence available.
      </p>

      <ul className="mt-5 space-y-3">
        {primary.map((q, i) => (
          <li key={i} className="flex gap-3">
            <span
              aria-hidden="true"
              className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
            />
            <span className="min-w-0">
              <span className="ib-breakable block text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                {q.question}
              </span>
              <span className="block text-xs text-[color:var(--ib-ink-3)]">
                {q.source}
              </span>
            </span>
          </li>
        ))}
      </ul>

      {rest.length > 0 && (
        <details className="mt-5">
          <summary className="cursor-pointer list-none text-sm text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            {rest.length} further question{rest.length === 1 ? "" : "s"} the
            council raised
          </summary>
          <ul className="mt-3 space-y-2">
            {rest.map((q, i) => (
              <li
                key={i}
                className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                {q.question}{" "}
                <span className="text-xs text-[color:var(--ib-ink-3)]">
                  — {q.source}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </Surface>
  );
}
