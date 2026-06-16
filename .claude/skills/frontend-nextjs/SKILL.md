# Frontend Next.js Agent Skill

## Role

You implement the web interface using Next.js App Router, React and TypeScript.

---

## Responsibilities

- Public-facing report pages and company pages
- Admin dashboard (report review, publishing, agent run inspection)
- User account pages (Version 2: preferences, portfolio, notifications)
- Reusable UI components
- API client integration layer
- Loading and error states
- Responsive layout (desktop and mobile)

---

## Architecture Rules

- Use TypeScript. No plain JavaScript files.
- Prefer reusable components. Extract shared UI into `components/`.
- Keep all API calls in a dedicated client layer under `lib/api/` — never fetch directly from page components.
- Never hardcode mock data in production UI paths. If needed during development, mark clearly with `// MOCK`.
- Public and personalized content must be separated at the route level.
- Admin routes must be protected — never render admin UI for unauthenticated or unauthorized users.
- Use Tailwind CSS for styling. No additional CSS frameworks unless explicitly approved.
- Prefer server components for data fetching, client components only where interactivity is required.

---

## Typical Files

```
apps/web/app/                   # Next.js App Router pages
apps/web/app/(public)/          # public-facing routes
apps/web/app/(admin)/           # admin dashboard routes
apps/web/app/(account)/         # authenticated user routes (V2)
apps/web/components/            # reusable React components
apps/web/lib/api/               # typed API client functions
apps/web/lib/hooks/             # custom React hooks
apps/web/types/                 # shared TypeScript types (frontend)
apps/web/public/                # static assets
```

---

## Key Pages

Version 1:
- `/` — homepage / landing
- `/reports` — public report archive
- `/reports/[slug]` — individual report
- `/companies/[ticker]` — company page
- `/themes/[slug]` — theme page
- `/admin` — admin dashboard
- `/admin/reports/[id]` — report review and publish/reject

Version 2 (future):
- `/account` — user settings
- `/account/portfolio` — portfolio input
- `/account/recommendations` — personalized recommendations

---

## API Integration Rules

- All API calls should be typed using shared types or local TypeScript interfaces.
- Handle loading states explicitly — never render empty UI without a loading indicator.
- Handle error states explicitly — never silently swallow API errors.
- Use environment variables for API base URL — never hardcode URLs.

---

## Definition of Done

- Page or component compiles without TypeScript errors
- Route renders correctly in browser (dev server test)
- API integration is typed and connected
- Loading and error states are handled
- Mobile layout is at least usable (not broken)
- `npm run typecheck` passes
- `npm run lint` passes
- `npm run build` passes
