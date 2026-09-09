# Safe Browsing phishing warning — technical audit and human action required

**Status:** technical audit CLEAN. Google's classification is **unverified** — confirming
it requires owner access to Google tooling that this process cannot reach.

**Host:** `ib-stg-web.azurewebsites.net`
**Audited:** 2026-09-08/09, against the deployed site, not repository source.

---

## 1. What was checked, and what was found

Every check below ran against the live deployment.

| Check | Result |
|---|---|
| Deployed SHA | `8e9c8ba`, an ancestor of `origin/main` with **zero** later `apps/web` commits |
| Deployment history | every entry `OneDeploy` from GitHub Actions, status success, timestamps matching known merges — **no unexpected deploy** |
| External scripts | **none** on `/`, `/login`, `/research` — all chunks are same-origin `/_next/static/` |
| iframes | **none** |
| Service workers | **none** |
| `/api/auth/dev-login` | **404** |
| `AUTH_TEST_MODE` | **absent** from the web app settings |
| Password / credential fields | **none anywhere** — the only action is an OAuth redirect |
| OAuth start | real `github.com/login/oauth/authorize`, `redirect_uri` pinned to this host |
| Open redirect | none — `callbackUrl` of `https://evil.example.com`, `//evil.example.com`, encoded and backslash variants all keep the app's own host |
| Post-auth redirect | `toSafeInternalPath` → `sanitizePath` rejects absolute URLs and `//` |
| Invalid OAuth state | redirects to own `/login?error=oauth_state_invalid` |
| Security headers | HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` |
| TLS | `httpsOnly: true` |
| Hostnames | only `ib-stg-web.azurewebsites.net` and its `.scm` — **no custom domain, no DNS surface** |

**Verdict: no evidence of compromise.** Nothing on the deployed site collects a
credential, loads third-party code, or redirects off-host.

## 2. The most plausible false-positive trigger

`/login` renders, on a **generic shared hosting domain**:

> Admin Sign In — InvestingBuddy · IB InvestingBuddy Admin · Internal workspace sign-in ·
> **Sign in to Admin** · Access is restricted to authorized administrators.

A branded corporate **admin sign-in** page, urging sign-in, on `*.azurewebsites.net` — a
suffix on the Public Suffix List shared by millions of tenants — is the shape of a
credential-phishing kit. Classifiers weight brand-like naming, sign-in intent, shared
hosting and low domain reputation. That the page has **no password field at all** is not
something an automated classifier necessarily establishes.

**A custom domain would reduce the risk of recurrence. It does not clear an existing
classification** — the flag attaches to the URL/host that was reported, and a new domain
starts with no reputation of its own.

## 3. What I could not determine, and why

* **Google Search Console** — needs a verified owner. No verification token or DNS record
  is configured for this host, and `*.azurewebsites.net` cannot be verified by DNS.
* **Safe Browsing Transparency Report** — JavaScript-rendered; the fetched HTML is a 7.8KB
  shell with no verdict in it.
* **Safe Browsing Lookup API** — needs a Google API key; none is configured.

So the **exact flagged URL and its scope (host-wide or path-specific) are unknown.** I
have not submitted a review and will not claim one was submitted.

---

## 4. What you need to do

### Step 1 — see the actual classification

Open in Chrome: **`https://transparencyreport.google.com/safe-browsing/search?url=ib-stg-web.azurewebsites.net`**

Record the verdict verbatim. Then repeat for the specific paths, because the flag may be
path-specific and that changes the remedy:

* `https://ib-stg-web.azurewebsites.net/`
* `https://ib-stg-web.azurewebsites.net/login`
* `https://ib-stg-web.azurewebsites.net/research`

Also note the exact URL Chrome shows in the red interstitial — the address bar and the
"Details" link name the URL that was actually reported.

### Step 2 — claim the property in Search Console

1. Go to **https://search.google.com/search-console**
2. **Add property → URL prefix** → `https://ib-stg-web.azurewebsites.net/`
   (URL-prefix, **not** Domain — a Domain property needs DNS TXT on `azurewebsites.net`,
   which Microsoft owns and you cannot set.)
3. Verify by **HTML file upload** or **HTML meta tag**. Both are within your control:
   * *Meta tag* — give me the token and I will add it to the app's `<head>` and deploy;
   * *HTML file* — give me the filename and contents and I will serve it from `public/`.
4. Open **Security & Manual Actions → Security Issues**.

That page names the **exact URLs** Google flagged and the category. Send me what it says.

### Step 3 — request the review

Only after Step 2 shows the issue. In **Security Issues**, click **Request Review** and
paste:

> This host serves an internal-only research tool for a single authorised administrator.
> It is not a public service and impersonates no organisation.
>
> There is no credential collection anywhere on the site: the sign-in page renders no
> password field and no form that accepts credentials. The only action is a redirect to
> GitHub's own OAuth endpoint (`github.com/login/oauth/authorize`), with the redirect URI
> pinned to this host. The OAuth callback cannot redirect to an external destination —
> non-relative and protocol-relative callback values are rejected.
>
> We audited the deployment on 2026-09-09 against the live site: no third-party scripts,
> no iframes, no service workers, no injected code; all assets are served same-origin
> from the application's own build. The deployed commit matches our CI build with no
> unexpected deployments in the history. Security headers include HSTS, nosniff,
> X-Frame-Options DENY and a referrer policy, and the site is HTTPS-only.
>
> We believe the classification was triggered by an administrator sign-in page presented
> on a shared `*.azurewebsites.net` hostname, which resembles a phishing pattern while
> collecting no credentials. We are evaluating a dedicated custom domain.

### Step 4 — do not declare it fixed

**The warning is resolved only when Chrome stops showing it.** Re-check the Transparency
Report and load the site in a clean Chrome profile. Reviews typically take a few days.

---

## 5. What was deliberately not done

* No attempt to bypass, suppress or work around Safe Browsing.
* No review submitted — that requires owner access I do not have.
* No custom domain provisioned — that is a paid, user-owned decision, and on its own it
  would not clear an existing classification.
