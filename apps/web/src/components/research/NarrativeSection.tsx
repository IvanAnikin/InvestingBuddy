import Surface from "@/components/product/Surface";
import type { NarrativeGroup } from "./reportView";

/**
 * A grouped argument block (bull case, bear case, risks).
 *
 * Empty is a real state and is stated plainly: a case with nothing behind it
 * says so, rather than rendering an empty card that a reader may take as "no
 * risks were found".
 */
export default function NarrativeSection({
  title,
  groups,
  emptyMessage,
  accent,
  id,
  testId,
}: {
  title: string;
  groups: NarrativeGroup[];
  emptyMessage: string;
  accent?: "positive" | "negative";
  id?: string;
  testId?: string;
}) {
  const dot =
    accent === "positive"
      ? "bg-emerald-400"
      : accent === "negative"
        ? "bg-rose-400"
        : "bg-[color:var(--ib-line-strong)]";

  return (
    <Surface as="section" className="p-6 sm:p-7" id={id} testId={testId}>
      <h2 className="flex items-center gap-2.5 text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
        {accent && (
          <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${dot}`} />
        )}
        {title}
      </h2>

      {groups.length === 0 ? (
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          {emptyMessage}
        </p>
      ) : (
        <div className="mt-4 space-y-5">
          {groups.map((group) => (
            <div key={group.label}>
              {groups.length > 1 && (
                <p className="mb-2 text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                  {group.label}
                </p>
              )}
              <ul className="space-y-2">
                {group.points.map((point, i) => (
                  <li
                    key={i}
                    className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                  >
                    <span
                      aria-hidden="true"
                      className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                    />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Surface>
  );
}
