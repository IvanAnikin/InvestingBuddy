import Link from "next/link";
import type { ReactNode } from "react";

type Tone = "primary" | "secondary" | "quiet";

const TONES: Record<Tone, string> = {
  primary:
    "bg-[color:var(--ib-ink)] text-[#060913] hover:bg-white",
  secondary:
    "border border-[color:var(--ib-line-strong)] text-[color:var(--ib-ink)] hover:bg-[color:var(--ib-surface-raised)]",
  quiet:
    "text-[color:var(--ib-ink-2)] hover:text-[color:var(--ib-ink)]",
};

/**
 * The product call-to-action. A solid ink-on-dark primary and a hairline
 * secondary — no gradient fills, so the two levels read as a hierarchy rather
 * than as decoration.
 */
export default function PrimaryCTA({
  href,
  children,
  tone = "primary",
  arrow = true,
  className = "",
  testId,
}: {
  href: string;
  children: ReactNode;
  tone?: Tone;
  /** Shows the trailing arrow that nudges right on hover/focus. */
  arrow?: boolean;
  className?: string;
  testId?: string;
}) {
  return (
    <Link
      href={href}
      data-testid={testId}
      className={`ib-arrow-host inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium transition-colors ${TONES[tone]} ${className}`.trim()}
    >
      {children}
      {arrow && (
        <span className="ib-arrow" aria-hidden="true">
          →
        </span>
      )}
    </Link>
  );
}
