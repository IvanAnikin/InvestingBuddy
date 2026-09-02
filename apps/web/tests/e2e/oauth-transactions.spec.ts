import { expect, test } from "@playwright/test";
import {
  __resetTransactionsForTest,
  claimTransaction,
  settleTransaction,
} from "../../src/lib/auth/oauth-transactions";

/**
 * Unit tests for the OAuth transaction registry (no browser, no dev server —
 * the module is dependency-free, like src/lib/auth/url.ts).
 *
 * The property under test is the one the live incident violated: an
 * authorization code must be exchangeable with GitHub exactly once, no matter
 * how many times the callback is reached with it.
 */

test.beforeEach(() => __resetTransactionsForTest());

const FP = "aaaaaaaaaaaa";

test("the first claim owns the code", async () => {
  expect(await claimTransaction(FP)).toEqual({ role: "owner" });
});

test("a later claim is a duplicate carrying the owner's success", async () => {
  await claimTransaction(FP);
  const identity = { email: "a@example.com", name: "A", dest: "/admin" };
  settleTransaction(FP, { status: "succeeded", identity });

  const second = await claimTransaction(FP);
  expect(second.role).toBe("duplicate");
  if (second.role !== "duplicate") throw new Error("unreachable");
  expect(second.outcome).toEqual({ status: "succeeded", identity });
});

test("a later claim carries the owner's failure, so it is not retried", async () => {
  await claimTransaction(FP);
  settleTransaction(FP, { status: "failed", reason: "no_verified_email" });

  const second = await claimTransaction(FP);
  expect(second.role).toBe("duplicate");
  if (second.role !== "duplicate") throw new Error("unreachable");
  expect(second.outcome).toEqual({
    status: "failed",
    reason: "no_verified_email",
  });
});

test("a duplicate arriving mid-flight waits for the owner's outcome", async () => {
  await claimTransaction(FP);
  const identity = { email: "a@example.com", name: "A", dest: "/research" };

  // Claim before the owner settles: it must block rather than exchange.
  const pending = claimTransaction(FP);
  let resolved = false;
  void pending.then(() => (resolved = true));
  await new Promise((r) => setTimeout(r, 50));
  expect(resolved).toBe(false);

  settleTransaction(FP, { status: "succeeded", identity });
  const second = await pending;
  expect(second.role).toBe("duplicate");
  if (second.role !== "duplicate") throw new Error("unreachable");
  expect(second.outcome).toEqual({ status: "succeeded", identity });
});

test("distinct codes are independent transactions", async () => {
  expect(await claimTransaction("code-one")).toEqual({ role: "owner" });
  expect(await claimTransaction("code-two")).toEqual({ role: "owner" });
});

test("only the owner's first settlement is recorded", async () => {
  await claimTransaction(FP);
  const identity = { email: "a@example.com", name: "A", dest: "/" };
  settleTransaction(FP, { status: "succeeded", identity });
  settleTransaction(FP, { status: "failed", reason: "later_write_ignored" });

  const second = await claimTransaction(FP);
  if (second.role !== "duplicate") throw new Error("expected duplicate");
  expect(second.outcome).toEqual({ status: "succeeded", identity });
});

test("settling an unknown code is a no-op, not a throw", () => {
  expect(() =>
    settleTransaction("never-claimed", { status: "failed", reason: "x" }),
  ).not.toThrow();
});

test("retention is bounded — old transactions are evicted", async () => {
  // Well past the 200-entry cap; the registry must not grow without limit.
  for (let i = 0; i < 260; i++) {
    await claimTransaction(`code-${i}`);
    settleTransaction(`code-${i}`, {
      status: "succeeded",
      identity: { email: "a@example.com", name: "A", dest: "/" },
    });
  }
  // The oldest entries are gone, so the code is claimable again (which the
  // callback answers as a clean recovery, never as a reused code).
  expect(await claimTransaction("code-0")).toEqual({ role: "owner" });
  // The newest is still remembered.
  const recent = await claimTransaction("code-259");
  expect(recent.role).toBe("duplicate");
});
