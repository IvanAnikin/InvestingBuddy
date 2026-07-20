import type { ReactNode } from "react";

/**
 * Glassmorphism panel — translucent, blurred, softly bordered surface used as
 * the base container across the modernized admin/marketing UI.
 */
export default function GlassCard({
  children,
  className = "",
  hover = false,
  as: Tag = "div",
  testId,
}: {
  children: ReactNode;
  className?: string;
  /** Adds a subtle lift + border highlight on hover. */
  hover?: boolean;
  as?: "div" | "section" | "article" | "li";
  /** Optional data-testid forwarded to the root element (for e2e tests). */
  testId?: string;
}) {
  const base =
    "rounded-2xl border border-white/10 bg-white/[0.045] backdrop-blur-xl " +
    "shadow-[0_8px_30px_rgba(2,6,23,0.45)]";
  const hoverCls = hover
    ? "transition-all duration-300 hover:-translate-y-0.5 hover:border-white/20 hover:bg-white/[0.07] hover:shadow-[0_14px_40px_rgba(2,6,23,0.55)]"
    : "";
  return (
    <Tag
      className={`${base} ${hoverCls} ${className}`.trim()}
      data-testid={testId}
    >
      {children}
    </Tag>
  );
}
