"use client";

import { useCallback, useEffect, useState } from "react";
import {
  buildDiscoveryCouncilView,
  councilLifecycle,
  type CouncilLifecycle,
  type DiscoveryCouncilView,
} from "@/components/research/discoveryCouncilView";
import {
  getDiscoveryCouncilReview,
  isNotFound,
  runDiscoveryCouncilReview,
} from "@/lib/api";
import type { DiscoveryCouncilReview } from "@/types/api";

/**
 * Read (and, on explicit request, start) the EXISTING run-level discovery
 * council review.
 *
 * The hook lives apart from the panel because the council's verdict is needed
 * in three places at once — the review panel, the comparison table and each
 * candidate card — and fetching it three times would be three council reads
 * for one page.
 *
 * Two rules it must not break. Mounting only READS: a council run costs real
 * tokens, so it starts when a person asks and never because a page rendered.
 * And polling happens only while a job is genuinely in flight, so an idle page
 * makes no repeating requests.
 */

const POLL_INTERVAL_MS = 4000;

/**
 * State is stamped with the run it belongs to, so switching runs shows nothing
 * rather than the previous run's council for a frame. Deriving that beats
 * clearing it in an effect, which would cascade a render on every mount.
 */
interface CouncilFetchState {
  runId: string | null;
  review: DiscoveryCouncilReview | null;
  loading: boolean;
  /** True when the backend says no council job has ever run for this run. */
  absent: boolean;
  error: string | null;
}

const IDLE: CouncilFetchState = {
  runId: null,
  review: null,
  loading: false,
  absent: false,
  error: null,
};

export interface DiscoveryCouncilState {
  review: DiscoveryCouncilReview | null;
  view: DiscoveryCouncilView;
  lifecycle: CouncilLifecycle;
  loading: boolean;
  starting: boolean;
  error: string | null;
  absent: boolean;
  start: () => Promise<void>;
}

export function useDiscoveryCouncil(runId: string | null): DiscoveryCouncilState {
  const [state, setState] = useState<CouncilFetchState>(IDLE);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async (id: string) => {
    try {
      const review = await getDiscoveryCouncilReview(id);
      setState({ runId: id, review, loading: false, absent: false, error: null });
    } catch (e) {
      const message = e instanceof Error ? e.message : "";
      // 404 is the honest "no council job has ever run here" answer, not an
      // error to alarm the reader with. The STATUS says so; the wording of the
      // message is the backend's to change.
      const notFound = isNotFound(e);
      setState({
        runId: id,
        review: null,
        loading: false,
        absent: notFound,
        error: notFound
          ? null
          : message || "Could not read the council review.",
      });
    }
  }, []);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void (async () => {
      setState({ ...IDLE, runId, loading: true });
      await load(runId);
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [runId, load]);

  // Only this run's state counts; a stale run's review is not shown at all.
  const current = state.runId === runId ? state : IDLE;
  const lifecycle = councilLifecycle(current.review);

  useEffect(() => {
    if (!runId || lifecycle !== "in_flight") return;
    let cancelled = false;
    const timer = setTimeout(() => {
      if (!cancelled) void load(runId);
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [runId, lifecycle, current.review, load]);

  const start = useCallback(async () => {
    if (!runId) return;
    setStarting(true);
    try {
      // The SAME endpoint, job and semantics the admin console uses.
      const review = await runDiscoveryCouncilReview(runId);
      setState({
        runId,
        review,
        loading: false,
        absent: false,
        error: null,
      });
    } catch (e) {
      setState((prev) => ({
        ...prev,
        runId,
        error:
          e instanceof Error
            ? e.message
            : "Could not start the council review.",
      }));
    } finally {
      setStarting(false);
    }
  }, [runId]);

  return {
    review: current.review,
    view: buildDiscoveryCouncilView(current.review),
    lifecycle,
    loading: current.loading,
    starting,
    error: current.error,
    absent: current.absent,
    start,
  };
}
