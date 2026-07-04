// Staging environment parameters
// Region: westeurope | Resource group: ib-stg-rg
//
// dbAdminPassword is read from the AZURE_STAGING_DB_PASSWORD environment variable.
// Set this variable before running az deployment:
//   export AZURE_STAGING_DB_PASSWORD=$(openssl rand -hex 16)
//   az deployment group create ... --parameters infra/azure/parameters/staging.bicepparam
//
// In GitHub Actions, AZURE_STAGING_DB_PASSWORD is stored as a repository secret.
//
// githubActionsPrincipalId: set to the App Registration object ID once
// ib-github-actions-stg is created (see infra/azure/README.md for steps).
// Leave empty ('') until then.
//
// DO NOT commit secrets, subscription IDs, or real passwords to this file.

using '../main.bicep'

param env = 'stg'
param location = 'westeurope'
param projectShort = 'ib'
param dbAdminPassword = readEnvironmentVariable('AZURE_STAGING_DB_PASSWORD', '')
param githubActionsPrincipalId = ''
// Set true while deploying with a Contributor-only account (no roleAssignments/write).
// After gaining Owner/User Access Administrator on ib-stg-rg, set false and re-deploy.
param skipRbac = true
// PostgreSQL in northeurope — westeurope is offer-restricted on this MSDN subscription.
// northeurope (Ireland) is EU/GDPR compliant and the next-closest region.
param dbLocation = 'northeurope'
// ib-stg-db had a failed westeurope ARM reservation from a previous deployment attempt.
// Using ib-stg-psql to avoid the InvalidResourceLocation name collision.
param dbServerNameOverride = 'ib-stg-psql'
