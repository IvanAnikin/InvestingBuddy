import { fetchHealth } from "@/lib/api";

/**
 * Says out loud when the workspace is talking to a non-real backend.
 *
 * A functional review of this UI was once run against the offline mock backend
 * without that being visible anywhere on screen, and its fixtures were
 * reasonably mistaken for real results — a selected company came back as a
 * different one, and a submitted thesis came back as an unrelated example.
 * Both were the fixture answering, not the UI misbehaving, but nothing on the
 * page said so.
 *
 * The signal is the backend's OWN `/health.environment`, so this cannot drift:
 * the mock reports `test`, a real deployment reports `development`/`staging`,
 * and there is no client-side flag to get wrong. When health cannot be read at
 * all this renders nothing rather than guessing — an unreachable backend is
 * reported by the page that needed it, honestly, not pre-empted here.
 */
export default async function PreviewDataNotice() {
  let environment: string | null = null;
  let version: string | null = null;
  try {
    const health = await fetchHealth();
    environment = health.environment;
    version = health.version;
  } catch {
    return null;
  }

  if (environment !== "test") return null;

  return (
    <div
      data-testid="preview-data-notice"
      className="border-b border-amber-400/25 bg-amber-500/[0.08] px-5 py-2 text-center text-xs text-amber-200 sm:px-8"
    >
      <strong className="font-semibold">Preview data.</strong> This workspace is
      connected to the offline test backend ({version}). Companies, runs and
      reports shown here are fixtures — not research, and not your data.
    </div>
  );
}
