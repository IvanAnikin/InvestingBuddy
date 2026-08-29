"use client";

import { useEffect, useRef, type ElementType, type ReactNode } from "react";

/**
 * Scroll-reveal wrapper.
 *
 * The hidden state is applied to the DOM node imperatively, and only after this
 * component has confirmed that motion is allowed. That ordering matters: it
 * means server-rendered HTML, a JS-disabled browser, and a
 * `prefers-reduced-motion` visitor all receive fully-visible content, and no
 * reader is ever left staring at an element that is invisible because an
 * animation never ran.
 *
 * Reveal is a DOM effect, not application state, so it is applied with
 * `classList` rather than by re-rendering: the component renders once, and only
 * `opacity`/`transform` change, which costs no layout.
 */
export default function Reveal({
  children,
  as: Tag = "div" as ElementType,
  className = "",
  /** Stagger, in ms, applied as a transition delay. */
  delay = 0,
}: {
  children: ReactNode;
  as?: ElementType;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const reduced = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (reduced || typeof IntersectionObserver === "undefined") return;

    node.classList.add("ib-reveal");
    if (delay) node.style.transitionDelay = `${delay}ms`;

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            node.classList.add("ib-revealed");
            observer.disconnect();
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [delay]);

  return (
    <Tag ref={ref} className={className}>
      {children}
    </Tag>
  );
}
