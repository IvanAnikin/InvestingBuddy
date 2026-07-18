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

const ERROR_MESSAGES: Record<string, string> = {
  oauth_not_configured: "Sign-in is not configured on this environment.",
  state_mismatch: "Your sign-in request expired or was invalid. Try again.",
  invalid_response: "The sign-in response was invalid. Try again.",
  token_exchange_failed: "Could not complete sign-in with the provider.",
  identity_lookup_failed: "Could not read your account identity. Try again.",
  no_verified_email: "Your provider account has no verified email.",
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
  const errorMessage = error ? ERROR_MESSAGES[error] ?? "Sign-in failed." : null;

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
                <p>{errorMessage}</p>
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
