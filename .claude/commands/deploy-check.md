# Deploy Check Command

You are verifying whether the InvestingBuddy repository is ready to deploy to a target environment.

---

## Pre-Check

Run:
```bash
git status
git log --oneline -5
git diff main...HEAD
```

Identify the target environment: local / staging / production.

---

## Verification Checklist

### Tests
- [ ] `pytest apps/api/tests/` passes
- [ ] `npm run build` passes in `apps/web/`
- [ ] No skipped tests that cover critical paths
- [ ] No known failing tests

### Code Quality
- [ ] `ruff check apps/api/` passes
- [ ] `mypy apps/api/` passes (or known type issues documented)
- [ ] `npm run typecheck` passes in `apps/web/`
- [ ] `npm run lint` passes in `apps/web/`

### Secrets
- [ ] No secrets committed to git (check `.env`, API keys, passwords)
- [ ] `.env.example` is up to date with all required variables
- [ ] All required GitHub Actions secrets are documented
- [ ] Azure Key Vault is configured for target environment

### Database
- [ ] All pending Alembic migrations are created
- [ ] Migration upgrade tested locally (`alembic upgrade head`)
- [ ] No manual `ALTER TABLE` applied outside Alembic

### GitHub Actions
- [ ] `api-ci.yml` exists and runs tests on PR
- [ ] `web-ci.yml` exists and runs build on PR
- [ ] Deployment workflows exist for target environment
- [ ] Deployment only triggers after CI passes

### Azure Resources
- [ ] All required Azure resources are provisioned (see docs/DEPLOYMENT.md)
- [ ] Azure App Service app settings match `.env.example` variables
- [ ] Azure Key Vault contains all production secrets
- [ ] Azure Database for PostgreSQL is reachable from App Service

### Health Endpoint
- [ ] `GET /health` returns 200 OK on the backend
- [ ] Frontend homepage loads without errors

### Documentation
- [ ] `docs/DEPLOYMENT.md` documents current deployment steps
- [ ] `docs/DEPLOYMENT.md` lists all required Azure resources
- [ ] Manual deployment steps are documented

---

## Output Format

```
## Deploy Check Result

### Target environment
<local / staging / production>

### Status
READY / NOT READY

### Blocking Issues
- [ ] <issue that must be resolved before deploying>

### Warnings (non-blocking)
- <issue to monitor>

### Required Secrets
- <secret name> — <where it should be stored>

### Required Azure Resources
- <resource name and type>

### Manual Steps Before Deploy
1. <step>
2. <step>

### Manual Steps After Deploy
1. Run migrations: `alembic upgrade head`
2. Verify health endpoint: `GET /health`
3. <other steps>
```
