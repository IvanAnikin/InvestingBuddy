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
