# Add API Endpoint Command

You are adding one backend API endpoint to the InvestingBuddy FastAPI backend.

---

## Pre-Implementation

1. Read `docs/API.md` to understand existing endpoint patterns
2. Read `.claude/skills/backend-fastapi/SKILL.md` for architecture rules
3. Identify the API tier: public, user (authenticated), or admin
4. Inspect the existing route files in `apps/api/app/api/`
5. Inspect existing services in `apps/api/app/services/`

---

## Implementation Steps

```
Step 1: Define the route
    - HTTP method and path
    - Authentication requirement
    - Rate limiting needs (flag for later)

Step 2: Define request schema (Pydantic)
    - Input validation rules
    - Field types and constraints
    - Optional vs required fields

Step 3: Define response schema (Pydantic)
    - All returned fields typed
    - No unintended private fields in public responses
    - Consistent structure with existing endpoints

Step 4: Add service method
    - Business logic in service, not in route handler
    - Async database operations
    - Error cases handled and raised as HTTPException

Step 5: Add route handler (thin)
    - Call service method
    - Return response schema
    - Annotate with OpenAPI metadata (summary, description, tags)

Step 6: Register route in router
    - Add to correct router file (public / user / admin)
    - Confirm router is included in main.py

Step 7: Add tests
    - Happy path integration test using httpx
    - Auth rejection test (if authenticated endpoint)
    - Input validation rejection test
    - Error case test

Step 8: Update docs/API.md
    - Add endpoint to the correct section
    - Include method, path, auth requirement, request and response summary
```

---

## Route Structure

```
GET  /api/reports           → public, list
GET  /api/reports/{slug}    → public, detail

POST /api/me/portfolio/positions     → user auth, create
GET  /api/me/portfolio               → user auth, read own data

GET    /api/admin/reports            → admin auth
POST   /api/admin/reports/{id}/publish → admin auth, action
```

---

## Rules

- Routes must be thin — no business logic in route handlers.
- Use the service layer for all business logic.
- Validate all inputs with Pydantic — never trust raw request data.
- Never return passwords, tokens, private keys or internal IDs unexpectedly.
- Public endpoints must not return user-private data.
- Admin endpoints must verify admin role.
- User endpoints must scope to the requesting user's data.

---

## Output Format

```
## Endpoint Summary

### Endpoint
<METHOD> <path>

### Auth required
None / User / Admin

### Request schema
<key fields>

### Response schema
<key fields>

### Files changed
- apps/api/app/api/<tier>/<router>.py
- apps/api/app/schemas/<name>.py
- apps/api/app/services/<name>_service.py
- apps/api/tests/integration/test_<name>.py
- docs/API.md

### Tests added
- <test description>

### Checks run
- pytest: passed/failed
- ruff: passed/failed
- mypy: passed/failed
```
