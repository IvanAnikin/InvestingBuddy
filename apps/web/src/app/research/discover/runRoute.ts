/**
 * The address of one discovery run.
 *
 * A run id is a UUID, so anything else in the `[run_id]` segment is a 404
 * rather than a request the backend would reject anyway. The path is built in
 * ONE place so the selector, the post-create navigation and the copied link can
 * never disagree about what a run's address is.
 */

const RUN_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const DISCOVERY_BASE_PATH = "/research/discover";

export function isDiscoveryRunId(value: unknown): value is string {
  return typeof value === "string" && RUN_ID_PATTERN.test(value);
}

export function discoveryRunPath(runId: string): string {
  return `${DISCOVERY_BASE_PATH}/${encodeURIComponent(runId)}`;
}
