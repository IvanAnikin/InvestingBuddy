/**
 * Keyboard skip link. Visually hidden until focused, then pinned to the top-left
 * so the first Tab on any product surface jumps past the navigation.
 */
export default function SkipLink({ href = "#main" }: { href?: string }) {
  return (
    <a
      href={href}
      className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-[color:var(--ib-ink)] focus:px-4 focus:py-2 focus:text-sm focus:font-medium focus:text-[#060913]"
    >
      Skip to content
    </a>
  );
}
