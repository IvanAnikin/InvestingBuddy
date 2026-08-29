import type { ReactNode } from "react";

/**
 * Section header used across the landing page and the research surfaces.
 *
 * The eyebrow is a plain, non-decorative label. Hierarchy comes from size and
 * colour weight only — there is no rule, badge or gradient, because on a long
 * page those accumulate into noise.
 */
export default function SectionHeading({
  eyebrow,
  title,
  lede,
  id,
  align = "left",
}: {
  eyebrow?: string;
  title: ReactNode;
  lede?: ReactNode;
  id?: string;
  align?: "left" | "center";
}) {
  const centered = align === "center";
  return (
    <div className={centered ? "mx-auto max-w-2xl text-center" : "max-w-2xl"}>
      {eyebrow && (
        <p className="mb-3 text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
          {eyebrow}
        </p>
      )}
      <h2
        id={id}
        className="text-2xl font-semibold tracking-tight text-[color:var(--ib-ink)] sm:text-3xl"
      >
        {title}
      </h2>
      {lede && (
        <p className="mt-3 text-base leading-relaxed text-[color:var(--ib-ink-2)]">
          {lede}
        </p>
      )}
    </div>
  );
}
