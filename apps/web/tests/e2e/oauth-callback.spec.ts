import { expect, test, type APIResponse, type BrowserContext } from "@playwright/test";

/**
 * GitHub OAuth callback — stale/replayed callback corrective.
 *
 * These run the REAL sign-in route against an offline provider that enforces
 * GitHub's actual rule (an authorization code works exactly once — see
 * tests/support/mock-backend.mjs). Nothing about our own callback is stubbed,
 * so the single-use semantics that produced the live failure are the ones under
 * test. No real GitHub credential, code or token exists anywhere in this file.
 *
 * THE FAILURE BEING REPRODUCED (captured on ib-stg-web, 2026-09-02 11:45-11:46):
 * one code arrived three times. The first arrival signed in successfully; 59
 * seconds later the same browser came back still holding the state cookie that
 * success had cleared and still without the session cookie it had set - the
 * browser had discarded that response instead of committing it. The retry then
 * re-exchanged a spent code and dead-ended on an error page.
 */

const MOCK_GITHUB = "http://127.0.0.1:8799/__mock_github__";

interface StartedFlow {
  /** The callback URL GitHub sent the browser back to (code + state). */
  callbackUrl: string;
  /** The cookie jar exactly as it was before the callback was processed. */
  cookiesBeforeCallback: Awaited<ReturnType<BrowserContext["cookies"]>>;
}

/**
 * Walk a sign-in as far as GitHub's redirect back to us, one hop at a time,
 * and stop before the callback is processed.
 */
async function startFlow(
  context: BrowserContext,
  callbackDest: string,
  email = "test-admin@example.com",
): Promise<StartedFlow> {
  const start = await context.request.get(
    `/api/auth/github?callbackUrl=${encodeURIComponent(callbackDest)}`,
    { maxRedirects: 0 },
  );
  expect(start.status()).toBe(303);
  const authorizeUrl = new URL(start.headers()["location"]);
  expect(authorizeUrl.toString()).toContain(MOCK_GITHUB);
  // Choose which account approves the app (the stand-in reads this).
  authorizeUrl.searchParams.set("ib_test_email", email);

  const approved = await context.request.get(authorizeUrl.toString(), {
    maxRedirects: 0,
  });
  expect(approved.status()).toBe(302);
  const callbackUrl = approved.headers()["location"];
  expect(callbackUrl).toContain("/api/auth/callback/github");

  return { callbackUrl, cookiesBeforeCallback: await context.cookies() };
}

/** Restore the jar to a snapshot - the browser having discarded a response. */
async function discardResponseCookies(
  context: BrowserContext,
  snapshot: StartedFlow["cookiesBeforeCallback"],
): Promise<void> {
  await context.clearCookies();
  await context.addCookies(snapshot);
}

async function sessionCookie(context: BrowserContext): Promise<string | null> {
  const all = await context.cookies();
  return all.find((c) => c.name === "ib_admin_session")?.value ?? null;
}

async function spentCodeCount(context: BrowserContext): Promise<number> {
  const res = await context.request.get(`${MOCK_GITHUB}/__spent_codes`);
  return (await res.json()).spent as number;
}

function locationPath(res: APIResponse): string {
  return new URL(res.headers()["location"], "http://localhost").pathname;
}

function locationSearch(res: APIResponse): URLSearchParams {
  return new URL(res.headers()["location"], "http://localhost").searchParams;
}

test.describe("a fresh sign-in", () => {
  test("6/7. correct state + fresh code creates a session and 303s to a clean URL", async ({
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    const res = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });

    expect(res.status()).toBe(303);
    expect(locationPath(res)).toBe("/admin");
    // The destination the user is left on carries no OAuth material.
    expect(res.headers()["location"]).not.toContain("code=");
    expect(res.headers()["location"]).not.toContain("state=");
    expect(await sessionCookie(context)).toBeTruthy();
  });

  test("8. the callback never renders application HTML under its own URL", async ({
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    const res = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);
    expect(res.headers()["content-type"] ?? "").not.toContain("text/html");
    expect(await res.text()).not.toContain("<html");
  });

  test("18/19. the callback response is no-store and sends no referrer", async ({
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    const res = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(res.headers()["cache-control"]).toContain("no-store");
    expect(res.headers()["referrer-policy"]).toBe("no-referrer");
  });

  test("1. each sign-in starts a brand-new transaction", async ({ context }) => {
    const first = await startFlow(context, "/admin");
    const second = await startFlow(context, "/admin");
    const stateOf = (url: string) =>
      new URL(url, "http://localhost").searchParams.get("state");
    const codeOf = (url: string) =>
      new URL(url, "http://localhost").searchParams.get("code");
    expect(stateOf(first.callbackUrl)).not.toBe(stateOf(second.callbackUrl));
    expect(codeOf(first.callbackUrl)).not.toBe(codeOf(second.callbackUrl));
  });
});

test.describe("the captured live failure", () => {
  test("16/22. a browser that discarded the winning response recovers into a session", async ({
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    const spentBefore = await spentCodeCount(context);

    // 1st arrival - succeeds on the server.
    const first = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(first.status()).toBe(303);
    expect(locationPath(first)).toBe("/admin");

    // The browser never commits that response: no session cookie was stored
    // and the state cookie it cleared is still there. This is the exact jar
    // state observed live 59 seconds after a successful sign-in.
    await discardResponseCookies(context, flow.cookiesBeforeCallback);
    expect(await sessionCookie(context)).toBeNull();

    // 2nd arrival of the SAME URL - before the fix this re-exchanged a spent
    // code and dead-ended on /login?error=code_already_used.
    const retry = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(retry.status()).toBe(303);
    expect(locationPath(retry)).toBe("/admin");
    expect(await sessionCookie(context)).toBeTruthy();

    // 9/16. And the code was never presented to the provider a second time.
    expect(await spentCodeCount(context)).toBe(spentBefore + 1);
  });

  test("the recovered session actually opens the private route", async ({
    context,
    page,
  }) => {
    const flow = await startFlow(context, "/admin");
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });
    await discardResponseCookies(context, flow.cookiesBeforeCallback);
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });

    await page.goto("/admin");
    expect(new URL(page.url()).pathname).toBe("/admin");
    await expect(page.locator("h1")).toContainText("Admin Dashboard");
  });
});

test.describe("state validation stays fail-closed", () => {
  test("10/13. a callback with no state cookie gets a clean recovery and no session", async ({
    browser,
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });

    // A different client that only has the URL - the anonymous fetcher that
    // hit the live callback 2.3s after the browser did.
    const stranger = await browser.newContext();
    const res = await stranger.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);
    expect(locationPath(res)).toBe("/login");
    expect(locationSearch(res).get("error")).toBe("oauth_state_invalid");
    expect(await sessionCookie(stranger)).toBeNull();
    await stranger.close();
  });

  test("12. a mismatched state is rejected", async ({ context }) => {
    const flow = await startFlow(context, "/admin");
    const tampered = new URL(flow.callbackUrl);
    tampered.searchParams.set("state", "00000000-0000-0000-0000-000000000000");
    const res = await context.request.get(tampered.toString(), {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);
    expect(locationSearch(res).get("error")).toBe("oauth_state_invalid");
    expect(await sessionCookie(context)).toBeNull();
  });

  test("11. an expired state cookie is rejected", async ({ context }) => {
    const flow = await startFlow(context, "/admin");
    // The state cookie is what expires; dropping it is what its Max-Age does.
    await context.clearCookies({ name: "ib_oauth_state" });
    const res = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);
    expect(locationSearch(res).get("error")).toBe("oauth_state_invalid");
    expect(await sessionCookie(context)).toBeNull();
  });

  test("a callback with no code at all is rejected", async ({ context }) => {
    const flow = await startFlow(context, "/admin");
    const noCode = new URL(flow.callbackUrl);
    noCode.searchParams.delete("code");
    const res = await context.request.get(noCode.toString(), {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);
    expect(locationPath(res)).toBe("/login");
    expect(await sessionCookie(context)).toBeNull();
  });

  test("a state-proven replay is the ONLY way a spent code yields a session", async ({
    browser,
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });

    // Same spent code, presented with a state cookie minted by a DIFFERENT
    // flow: the transaction is remembered, but ownership is not proven.
    const attacker = await browser.newContext();
    await startFlow(attacker, "/admin");
    const res = await attacker.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);
    expect(locationSearch(res).get("error")).toBe("oauth_state_invalid");
    expect(await sessionCookie(attacker)).toBeNull();
    await attacker.close();
  });
});

test.describe("duplicate and stale arrivals", () => {
  test("17. two simultaneous callbacks spend the code exactly once", async ({
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    const spentBefore = await spentCodeCount(context);

    const [a, b] = await Promise.all([
      context.request.get(flow.callbackUrl, { maxRedirects: 0 }),
      context.request.get(flow.callbackUrl, { maxRedirects: 0 }),
    ]);

    // Deterministic: both are answered, neither is an error page.
    for (const res of [a, b]) {
      expect(res.status()).toBe(303);
      expect(locationPath(res)).toBe("/admin");
    }
    expect(await spentCodeCount(context)).toBe(spentBefore + 1);
  });

  test("10. a stale callback after a completed sign-in never retries the code", async ({
    context,
  }) => {
    const flow = await startFlow(context, "/admin");
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });
    const spentAfterSignIn = await spentCodeCount(context);

    // The normal reload/back case: the success response DID commit, so the
    // state cookie is gone and the session is present.
    const stale = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(stale.status()).toBe(303);
    expect(locationPath(stale)).toBe("/login");
    expect(stale.headers()["location"]).not.toContain("code=");
    expect(await spentCodeCount(context)).toBe(spentAfterSignIn);
  });

  test("a stale callback leaves an already-signed-in user signed in", async ({
    context,
    page,
  }) => {
    const flow = await startFlow(context, "/admin");
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });
    const before = await sessionCookie(context);

    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });
    expect(await sessionCookie(context)).toBe(before);

    // 15. And the clean error URL offers a NEW sign-in, not a retry.
    await page.goto("/login?error=oauth_callback_expired");
    // The self-heal sends a signed-in admin straight on to their destination.
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("15. the error page offers a fresh transaction to a signed-out user", async ({
    browser,
  }) => {
    const visitor = await browser.newContext();
    const page = await visitor.newPage();
    await page.goto("/login?error=oauth_callback_expired");
    await expect(page.getByTestId("login-error")).toContainText(
      "Your previous sign-in attempt expired",
    );

    const fresh = await visitor.request.get("/api/auth/github", {
      maxRedirects: 0,
    });
    expect(fresh.status()).toBe(303);
    expect(fresh.headers()["location"]).toContain("state=");
    await visitor.close();
  });
});

test.describe("authorization is unchanged by the recovery path", () => {
  test("23. a non-allowlisted account signs in but stays unauthorized", async ({
    context,
    page,
  }) => {
    const flow = await startFlow(context, "/admin", "outsider@example.com");
    const res = await context.request.get(flow.callbackUrl, {
      maxRedirects: 0,
    });
    expect(res.status()).toBe(303);

    await page.goto("/admin");
    expect(new URL(page.url()).pathname).toBe("/unauthorized");
  });

  test("24. an allowlisted account is authorized after a recovered replay", async ({
    context,
    page,
  }) => {
    const flow = await startFlow(context, "/research", "test-admin@example.com");
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });
    await discardResponseCookies(context, flow.cookiesBeforeCallback);
    await context.request.get(flow.callbackUrl, { maxRedirects: 0 });

    await page.goto("/research");
    expect(new URL(page.url()).pathname).toBe("/research");
  });
});
