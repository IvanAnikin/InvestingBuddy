import type { ReactNode } from "react";
import DiscoverShell from "./DiscoverShell";
import DiscoveryRoute from "./DiscoveryRoute";

/**
 * The workbench lives in the LAYOUT, not in either page.
 *
 * A layout stays mounted while its child route changes, so moving from
 * `/research/discover` to `/research/discover/<run id>` — after creating a run,
 * picking another one, or with the browser's back and forward buttons — keeps
 * the draft description the reader was typing and does not flash an empty
 * page. The pages below only validate the address and name the tab; which run
 * is shown is read from the URL by `DiscoveryRoute`.
 */
export default function DiscoverLayout({ children }: { children: ReactNode }) {
  return (
    <DiscoverShell>
      {children}
      <DiscoveryRoute />
    </DiscoverShell>
  );
}
