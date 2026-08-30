import type { ReactNode } from "react";

/**
 * The product-surface card primitive.
 *
 * Deliberately quieter than the admin chrome's `GlassCard`: a hairline border
 * over a near-transparent fill, no blur, no drop shadow. On a research surface
 * the content is the signal, so the container should recede.
 */
export default function Surface({
  children,
  className = "",
  hover = false,
  as: Tag = "div",
  testId,
  id,
}: {
  children: ReactNode;
  className?: string;
  /** Adds the shared lift + border highlight on hover (disabled by reduced-motion). */
  hover?: boolean;
  as?: "div" | "section" | "article" | "li" | "aside" | "header";
  testId?: string;
  id?: string;
}) {
  return (
    <Tag
      id={id}
      data-testid={testId}
      className={`ib-panel ${hover ? "ib-panel-hover" : ""} ${className}`.trim()}
    >
      {children}
    </Tag>
  );
}
