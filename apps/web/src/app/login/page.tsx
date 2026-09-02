import { redirect } from "next/navigation";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import { getServerSession } from "@/lib/auth/server";
import { isTestAuthMode } from "@/lib/auth/session";
import { safeCallbackPath } from "@/lib/auth/url";

// Phase 23 — Admin/Auth Hardening. Internal admin sign-in. This page is public
// (unauthenticated) so users can reach it; it never exposes any credential or
// backend detail. It produces no investment output of any kind.
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Admin Sign In — InvestingBuddy",
};

// What the user is told when a sign-in did not complete.
//
// Every message says the same two things: the previous attempt is over, and the
// button below starts a new one. None of them ask the user to interpret a
// provider slug — `code_already_used` and friends stay in the logs, which is
// where they are diagnostic. The three legacy keys are still mapped so an error
// URL held in a tab from before this change still renders a sentence.
const ERROR_MESSAGES: Record<string, string> = {
  oauth_callback_expired:
    "Your previous sign-in attempt expired. Start a new sign-in below.",
  oauth_state_invalid:
    "That sign-in attempt is no longer valid. Start a new sign-in below.",
  oauth_provider_error:
    "The sign-in provider could not complete this attempt. Start a new sign-in below.",
  oauth_user_not_authorized:
    "That account is not authorized for this workspace.",
  oauth_internal_error: "Sign-in is temporarily unavailable. Try again later.",
  oauth_not_configured: "Sign-in is not configured on this environment.",
  invalid_response:
    "That sign-in attempt is no longer valid. Start a new sign-in below.",
  token_exchange_unreachable:
    "Could not reach the sign-in provider. Try again in a moment.",
  oauth_client_rejected:
    "The provider rejected this deployment's sign-in credentials. Contact an administrator.",
  redirect_uri_mismatch:
    "The sign-in redirect address is misconfigured. Contact an administrator.",
  identity_lookup_failed: "Could not read your account identity. Try again.",
  no_verified_email: "Your provider account has no verified email.",
  // Superseded reason codes, kept so an older error URL still reads sensibly.
  state_mismatch:
    "That sign-in attempt is no longer valid. Start a new sign-in below.",
  code_already_used:
    "Your previous sign-in attempt expired. Start a new sign-in below.",
  token_exchange_failed:
    "The sign-in provider could not complete this attempt. Start a new sign-in below.",
  session_unavailable: "Sign-in is temporarily unavailable. Try again later.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ callbackUrl?: string; error?: string }>;
}) {
  const { callbackUrl: rawCallback, error } = await searchParams;
  const callbackUrl = safeCallbackPath(rawCallback);

  // Already signed in and allowed → go straight to the destination.
  const session = await getServerSession();
  if (session?.allowed) {
    redirect(callbackUrl);
  }

  const testMode = isTestAuthMode();
  const errorMessage = error
    ? ERROR_MESSAGES[error] ??
      "That sign-in attempt did not complete. Start a new sign-in below."
    : null;

  return (
    <main className="relative mx-auto flex min-h-screen max-w-lg flex-col justify-center px-6 py-16">
      <div className="ib-fade-up">
        <div className="mb-8 flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-sky-500 to-violet-500 text-sm font-bold text-white shadow-lg shadow-sky-500/20">
            IB
          </span>
          <div>
            <p className="text-lg font-semibold text-slate-100">
              InvestingBuddy Admin
            </p>
            <p className="text-xs text-slate-500">Internal workspace sign-in</p>
          </div>
        </div>

        <GlassCard className="p-6">
          <h1 className="text-xl font-semibold text-white">Sign in to Admin</h1>
          <p className="mt-2 text-sm text-slate-400">
            This is an internal-only research workspace. Access is restricted to
            authorized administrators. Nothing here is investment advice, and no
            reports are published to the public.
          </p>

          {errorMessage && (
            <div className="mt-4">
              <SafetyBanner variant="warning" title="Sign-in problem">
                <p data-testid="login-error">{errorMessage}</p>
              </SafetyBanner>
            </div>
          )}

          {session && !session.allowed && (
            <div className="mt-4">
              <SafetyBanner variant="warning" title="Account not authorized">
                <p>
                  You are signed in as{" "}
                  <span className="font-mono">{session.email}</span>, which is
                  not an authorized admin.{" "}
                  <a
                    href="/api/auth/signout"
                    className="text-sky-300 underline hover:text-sky-200"
                  >
                    Sign out
                  </a>{" "}
                  to switch accounts.
                </p>
              </SafetyBanner>
            </div>
          )}

          <div className="mt-6 space-y-4">
            <a
              href={`/api/auth/github?callbackUrl=${encodeURIComponent(callbackUrl)}`}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/25 transition-all hover:-translate-y-0.5 hover:shadow-sky-500/40"
            >
              Sign in to Admin
            </a>

            {testMode && (
              <form
                method="POST"
                action="/api/auth/dev-login"
                className="space-y-2 rounded-xl border border-white/10 bg-white/[0.03] p-4"
              >
                <p className="text-xs font-semibold uppercase tracking-wide text-amber-300/80">
                  Test / local sign-in (AUTH_TEST_MODE)
                </p>
                <input type="hidden" name="callbackUrl" value={callbackUrl} />
                <label className="block text-xs text-slate-400" htmlFor="email">
                  Admin email
                </label>
                <input
                  id="email"
                  name="email"
                  type="email"
                  required
                  placeholder="admin@example.com"
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40"
                />
                <button
                  type="submit"
                  className="w-full rounded-lg border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-slate-100 transition-colors hover:bg-white/10"
                >
                  Continue (test mode)
                </button>
              </form>
            )}
          </div>
        </GlassCard>

        <p className="mt-6 text-center text-xs text-slate-600">
          InvestingBuddy · Internal research platform · Not investment advice ·
          No public reports are published.
        </p>
      </div>
    </main>
  );
}
