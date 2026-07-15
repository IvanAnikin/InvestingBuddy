import type { ReactNode } from "react";

type Variant = "danger" | "warning" | "info";

const VARIANTS: Record<Variant, { wrap: string; title: string; icon: string }> =
  {
    danger: {
      wrap: "border-rose-400/25 bg-rose-500/[0.09]",
      title: "text-rose-200",
      icon: "🔒",
    },
    warning: {
      wrap: "border-amber-400/25 bg-amber-500/[0.09]",
      title: "text-amber-200",
      icon: "⚠",
    },
    info: {
      wrap: "border-sky-400/25 bg-sky-500/[0.09]",
      title: "text-sky-200",
      icon: "ℹ",
    },
  };

/**
 * Compliance / safety banner used to keep the mandatory internal-only,
 * not-investment-advice, human-review-required warnings prominent across the
 * modernized UI. The exact disclaimer copy is passed in by each page so the
 * required wording is preserved verbatim.
 */
export default function SafetyBanner({
  variant = "danger",
  title,
  children,
  className = "",
}: {
  variant?: Variant;
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  const v = VARIANTS[variant];
  return (
    <div
      role="note"
      className={`rounded-xl border ${v.wrap} px-4 py-3 backdrop-blur-md ${className}`.trim()}
    >
      {title && (
        <p className={`mb-1 flex items-center gap-2 text-sm font-semibold ${v.title}`}>
          <span aria-hidden="true">{v.icon}</span>
          {title}
        </p>
      )}
      <div className="text-xs text-slate-300/90">{children}</div>
    </div>
  );
}
