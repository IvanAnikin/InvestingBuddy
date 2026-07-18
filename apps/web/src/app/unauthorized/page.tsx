import Link from "next/link";
import GlassCard from "@/components/ui/GlassCard";
import { getServerSession } from "@/lib/auth/server";

// Phase 23 — Admin/Auth Hardening. Shown to an authenticated user whose email
// is not on the admin allowlist. Public route (no session required to render).
export const dynamic = "force-dynamic";

export const metadata = {
  title: "Unauthorized — InvestingBuddy Admin",
};

export default async function UnauthorizedPage() {
  const session = await getServerSession();

  return (
    <main className="relative mx-auto flex min-h-screen max-w-lg flex-col justify-center px-6 py-16">
      <div className="ib-fade-up">
        <GlassCard className="p-6">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-rose-400/20 bg-rose-950/40 px-3 py-1 text-xs font-medium uppercase tracking-wide text-rose-200">
            Unauthorized
          </div>
          <h1 className="text-xl font-semibold text-white">
            You don&apos;t have access to InvestingBuddy Admin
          </h1>
          <p className="mt-2 text-sm text-slate-400">
            {session?.email ? (
              <>
                You are signed in as{" "}
                <span className="font-mono text-slate-200">
                  {session.email}
                </span>
                , but this account is not on the authorized admin allowlist.
              </>
            ) : (
              <>This account is not authorized to access the admin workspace.</>
            )}{" "}
            The admin workspace is internal-only and is not investment advice.
          </p>

          <div className="mt-6 flex flex-wrap gap-3">
            <a
              href="/api/auth/signout"
              className="rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-sky-500/25 transition-all hover:-translate-y-0.5"
            >
              Sign out &amp; switch account
            </a>
            <Link
              href="/"
              className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-slate-100 transition-colors hover:bg-white/10"
            >
              Back to home
            </Link>
          </div>
        </GlassCard>

        <p className="mt-6 text-center text-xs text-slate-600">
          If you believe you should have access, ask an existing admin to add
          your email to the allowlist.
        </p>
      </div>
    </main>
  );
}
