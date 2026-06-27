// App Service Plan + App Services for API (Python 3.12) and Web (Node 22)
//
// Cost-optimised for early staging: both apps share one B1 Linux plan (~$14/month).
// Azure App Service Plans are runtime-agnostic at the plan level — each App Service
// declares its own linuxFxVersion (PYTHON|3.12 or NODE|22-lts) independently.
//
// Scale-up path when traffic grows:
//   Change sharedPlan sku.name from 'B1' to 'B2' or 'P1v3' in this file,
//   or split into separate plans by reverting to two plan resources.
//
// Both apps use system-assigned managed identity for Key Vault access.
// Secrets referenced via @Microsoft.KeyVault() app setting syntax.

@description('Azure region')
param location string

@description('API App Service name')
param apiAppName string

@description('Web App Service name')
param webAppName string

@description('Shared App Service Plan name (hosts both API and Web)')
param sharedPlanName string

@description('Key Vault URI (with trailing slash) for secret references')
param kvUri string

@description('Application Insights connection string')
param appInsightsConnectionString string

// ── Shared App Service Plan (B1 Linux) ────────────────────────────────────
// B1: 1 vCore, 1.75 GB RAM. Adequate for early staging / low traffic.
// Both apps share this plan — no separate B2 plan needed at this stage.

resource sharedPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: sharedPlanName
  location: location
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  kind: 'linux'
  properties: {
    reserved: true
  }
}

// ── API App Service (Python 3.12) ─────────────────────────────────────────

resource apiApp 'Microsoft.Web/sites@2023-12-01' = {
  name: apiAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: sharedPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: true
      healthCheckPath: '/health'
      // gunicorn installed via pyproject.toml [deploy] extra
      appCommandLine: 'gunicorn -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 2 --timeout 120 app.main:app'
      appSettings: [
        { name: 'APP_ENV', value: 'staging' }
        { name: 'LLM_PROVIDER', value: 'mock' }
        { name: 'FINANCIAL_DATA_PROVIDER', value: 'mock' }
        { name: 'AZURE_OPENAI_API_VERSION', value: '2025-01-01-preview' }
        { name: 'AZURE_OPENAI_DEPLOYMENT_NAME', value: 'gpt-4.1-mini' }
        { name: 'AZURE_OPENAI_ENDPOINT', value: 'https://ib-stg-openai-d52d2.openai.azure.com/' }
        { name: 'AZURE_STORAGE_CONTAINER_NAME', value: 'investingbuddy-documents' }
        { name: 'WEBSITE_RUN_FROM_PACKAGE', value: '1' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        // Secrets via Key Vault references — managed identity must have Secrets User role
        {
          name: 'DATABASE_URL'
          value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/database-url/)'
        }
        {
          name: 'SECRET_KEY'
          value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/secret-key/)'
        }
        {
          name: 'AZURE_OPENAI_API_KEY'
          value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/openai-api-key/)'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'STAGING_BASIC_AUTH'
          value: '@Microsoft.KeyVault(SecretUri=${kvUri}secrets/staging-basic-auth/)'
        }
      ]
    }
  }
}

// ── Web App Service (Node.js 22) ──────────────────────────────────────────
// Shares the same plan as the API — different runtime, same compute pool.

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: webAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: sharedPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'NODE|22-lts'
      appCommandLine: 'next start'
      appSettings: [
        // NEXT_PUBLIC_API_BASE_URL is baked into the build at CI time (Next.js public var)
        // This runtime setting covers SSR calls from the server side
        { name: 'NEXT_PUBLIC_API_BASE_URL', value: 'https://${apiAppName}.azurewebsites.net' }
        { name: 'WEBSITE_RUN_FROM_PACKAGE', value: '1' }
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'false' }
        { name: 'NODE_ENV', value: 'production' }
      ]
    }
  }
}

output apiManagedIdentityPrincipalId string = apiApp.identity.principalId
output webManagedIdentityPrincipalId string = webApp.identity.principalId
output apiDefaultHostname string = apiApp.properties.defaultHostName
output webDefaultHostname string = webApp.properties.defaultHostName
