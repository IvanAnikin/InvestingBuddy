// GitHub OAuth endpoint resolution.
//
// The real endpoints are constants. They are redirectable to a local stand-in
// ONLY when AUTH_TEST_MODE=true — the same hard gate that guards the dev-login
// route — so a deployment can never have its token exchange (and with it the
// client secret) pointed at another host by an environment variable alone.
// AUTH_TEST_MODE is absent on ib-stg-web, which makes the override inert there.
//
// This exists so the callback's real behaviour — including GitHub's one-time
// code semantics, the thing this whole corrective is about — can be exercised
// end to end offline, instead of being asserted about a mock of our own code.

const REAL = {
  authorize: "https://github.com/login/oauth/authorize",
  token: "https://github.com/login/oauth/access_token",
  user: "https://api.github.com/user",
  emails: "https://api.github.com/user/emails",
} as const;

export interface GithubEndpoints {
  authorize: string;
  token: string;
  user: string;
  emails: string;
}

export function githubEndpoints(): GithubEndpoints {
  if (process.env.AUTH_TEST_MODE !== "true") return { ...REAL };
  const base = process.env.AUTH_GITHUB_TEST_BASE_URL;
  if (!base) return { ...REAL };
  const root = base.replace(/\/$/, "");
  return {
    authorize: `${root}/login/oauth/authorize`,
    token: `${root}/login/oauth/access_token`,
    user: `${root}/user`,
    emails: `${root}/user/emails`,
  };
}
