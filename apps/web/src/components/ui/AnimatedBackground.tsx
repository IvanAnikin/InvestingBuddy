/**
 * Decorative, non-interactive animated aurora background.
 *
 * Rendered once at the root layout so every page sits on the same soft,
 * slowly-drifting gradient field. Pure CSS animation (see globals.css) that is
 * fully disabled under `prefers-reduced-motion`. It is `aria-hidden` and never
 * captures pointer events, so it cannot interfere with the UI.
 */
export default function AnimatedBackground() {
  return (
    <div className="ib-bg" aria-hidden="true">
      <div className="ib-bg-blob ib-bg-blob-1" />
      <div className="ib-bg-blob ib-bg-blob-2" />
      <div className="ib-bg-blob ib-bg-blob-3" />
      <div className="ib-bg-grid" />
    </div>
  );
}
