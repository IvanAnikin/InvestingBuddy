// Build metadata for the web app.
//
// These identifiers are baked into the bundle at build time via NEXT_PUBLIC_*
// environment variables (see .github/workflows/deploy-web-staging.yml). Next.js
// statically inlines `process.env.NEXT_PUBLIC_*` references at build time, so the
// values are available at runtime on Azure App Service *without* needing any
// runtime App Service configuration — which is exactly why they are reliable for
// verifying which build is actually serving after a WEBSITE_RUN_FROM_PACKAGE
// deploy.
//
// SAFETY: only public, non-sensitive build identifiers are exposed here — commit
// SHA, CI run id, build timestamp, and environment name. No secrets, tokens, API
// keys, connection strings, or credentials are ever read or returned.
//
// A plain runtime `process.env.COMMIT_SHA` fallback is also supported for the
// case where the value is instead provided as an App Service application setting.
// Missing values degrade to the safe placeholder "unknown".

export interface BuildInfo {
  app: "investingbuddy-web";
  commit_sha: string;
  build_id: string;
  build_time: string;
  environment: string;
}

const PLACEHOLDER = "unknown";

function firstNonEmpty(...values: Array<string | undefined>): string {
  for (const value of values) {
    if (typeof value === "string" && value.trim() !== "") {
      return value.trim();
    }
  }
  return PLACEHOLDER;
}

/**
 * Returns the build metadata for the currently running web bundle.
 *
 * Prefers the build-time inlined `NEXT_PUBLIC_*` values, then any runtime App
 * Service application setting, then a safe `"unknown"` placeholder. Never throws
 * and never returns anything other than build identifiers.
 */
export function getBuildInfo(): BuildInfo {
  return {
    app: "investingbuddy-web",
    commit_sha: firstNonEmpty(
      process.env.NEXT_PUBLIC_COMMIT_SHA,
      process.env.COMMIT_SHA,
    ),
    build_id: firstNonEmpty(
      process.env.NEXT_PUBLIC_BUILD_ID,
      process.env.BUILD_ID,
    ),
    build_time: firstNonEmpty(
      process.env.NEXT_PUBLIC_BUILD_TIME,
      process.env.BUILD_TIME,
    ),
    environment: firstNonEmpty(
      process.env.NEXT_PUBLIC_APP_ENV,
      process.env.APP_ENV,
      "development",
    ),
  };
}
