// Replay-safe GitHub OAuth callback transactions.
//
// WHY THIS EXISTS — the failure it fixes was captured live on ib-stg-web
// (2026-09-02 11:45–11:46 UTC, one authorization code, three arrivals):
//
//   11:45:07  callback_received  state_ok=true   → token_exchange ok=true
//   11:45:08  signed_in                          → 3xx + Set-Cookie session
//                                                       + Set-Cookie state="" (clear)
//   11:45:10  callback_received  state_ok=false  ← a DIFFERENT client (no cookies)
//   11:46:06  callback_received  state_ok=true   ← the user's browser again,
//                                 had_session=false, ORIGINAL state cookie intact
//                                                → bad_verification_code
//
// The third line is the proof. Fifty-nine seconds after a *successful* sign-in
// the same browser came back still holding the state cookie the success
// response had cleared, and still without the session cookie that same response
// had set. Both Set-Cookie headers ride the SAME response, so neither taking
// effect means the browser discarded that response wholesale — the navigation
// was cancelled client-side (Chrome's Safe Browsing interstitial was on screen)
// rather than committed. The server had done everything right and the user was
// left signed out, holding a URL whose one-time code was already spent.
//
// GitHub authorization codes are single-use by design and that is not
// negotiable — this module never retries a spent code. Instead the outcome of a
// successful exchange is remembered briefly, so a second arrival of the same
// code can be *answered from memory* rather than re-exchanged.
//
// SECURITY MODEL
// --------------
// A replay is honoured only when the caller also presents the matching CSRF
// state cookie. That is not a weakening — it is the same proof of ownership the
// first exchange required, and it is exactly what separates the two repeat
// arrivals above: the user's own browser had it, the anonymous fetcher at
// 11:45:10 did not and got nothing. The invariant that makes this sound is that
// the session cookie and the state-clearing cookie are set on one response: a
// browser can never be missing the session yet have already dropped the state,
// so "state still valid" reliably identifies the discarded-response case.
//
// Records hold the verified identity, never a minted session token — the replay
// re-signs a fresh session rather than re-serving a stored bearer credential.
//
// SCOPE: process-local and bounded (TTL + hard cap). ib-stg-web runs a single
// instance; on a cold start or a second instance the memory is simply empty and
// the callback degrades to the clean recovery path, never to a broken one.

/** Matches the OAUTH_STATE_COOKIE Max-Age: past this a retry cannot pass state. */
const TRANSACTION_TTL_MS = 600_000;

/** Hard ceiling on retained transactions (bounded memory, oldest evicted first). */
const MAX_TRANSACTIONS = 200;

/** How long a duplicate callback waits for an in-flight exchange to resolve. */
const INFLIGHT_WAIT_MS = 10_000;

export interface OAuthIdentity {
  email: string;
  name: string;
  /** The sanitized post-login destination decided by the winning request. */
  dest: string;
}

type Outcome =
  | { status: "succeeded"; identity: OAuthIdentity }
  | { status: "failed"; reason: string };

interface Transaction {
  createdAt: number;
  /** Resolves when the owning request has finished its token exchange. */
  settled: Promise<Outcome>;
  resolve: (outcome: Outcome) => void;
  outcome: Outcome | null;
}

const transactions = new Map<string, Transaction>();

function prune(now: number): void {
  for (const [key, tx] of transactions) {
    if (now - tx.createdAt > TRANSACTION_TTL_MS) transactions.delete(key);
  }
  // Map preserves insertion order, so the head is the oldest entry.
  while (transactions.size > MAX_TRANSACTIONS) {
    const oldest = transactions.keys().next();
    if (oldest.done) break;
    transactions.delete(oldest.value);
  }
}

export type ClaimResult =
  | { role: "owner" }
  | { role: "duplicate"; outcome: Outcome };

/**
 * Claim the right to exchange `codeFp` with GitHub.
 *
 * The first caller becomes the owner and must report back via
 * {@link settleTransaction}. Any later caller for the same code is a duplicate:
 * it never touches GitHub, and instead receives the owner's outcome — waiting
 * briefly if the owner is still mid-flight (the near-simultaneous two-tab /
 * retry case), so two requests can never spend one code.
 */
export async function claimTransaction(codeFp: string): Promise<ClaimResult> {
  const now = Date.now();
  prune(now);

  const existing = transactions.get(codeFp);
  if (existing) {
    if (existing.outcome) return { role: "duplicate", outcome: existing.outcome };
    // Owner still exchanging: wait, but never longer than the bound — a hung
    // owner must not hang the duplicate too.
    const timeout = new Promise<Outcome>((resolve) =>
      setTimeout(
        () => resolve({ status: "failed", reason: "exchange_in_progress" }),
        INFLIGHT_WAIT_MS,
      ),
    );
    return {
      role: "duplicate",
      outcome: await Promise.race([existing.settled, timeout]),
    };
  }

  let resolve!: (outcome: Outcome) => void;
  const settled = new Promise<Outcome>((r) => {
    resolve = r;
  });
  transactions.set(codeFp, { createdAt: now, settled, resolve, outcome: null });
  return { role: "owner" };
}

/** Record how the owning request's exchange ended, releasing any duplicate. */
export function settleTransaction(codeFp: string, outcome: Outcome): void {
  const tx = transactions.get(codeFp);
  if (!tx || tx.outcome) return;
  tx.outcome = outcome;
  tx.resolve(outcome);
}

/** Test-only reset so specs do not leak transactions into one another. */
export function __resetTransactionsForTest(): void {
  transactions.clear();
}
