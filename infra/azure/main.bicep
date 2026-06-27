// InvestingBuddy — Azure Staging Infrastructure
// Region: westeurope | Resource group: ib-stg-rg
// Deploy: az deployment group create --resource-group ib-stg-rg \
//           --template-file infra/azure/main.bicep \
//           --parameters infra/azure/parameters/staging.bicepparam \
//           --parameters dbAdminPassword=<password-from-key-vault>
//
// WARNING: This template targets STAGING only (ib-stg-rg).
// Do NOT target ib-prod-rg or any production resource group.

targetScope = 'resourceGroup'

// ── Parameters ────────────────────────────────────────────────────────────

@description('Environment tag — stg or prod')
param env string = 'stg'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Project short name used in resource naming')
param projectShort string = 'ib'

@secure()
@description('PostgreSQL admin password. Generate: openssl rand -hex 16. Store result in Key Vault as db-password.')
param dbAdminPassword string

@description('GitHub Actions App Registration principal ID (object ID). Set to activate KV Secrets Officer role assignment.')
param githubActionsPrincipalId string = ''

// ── Resource Names ─────────────────────────────────────────────────────────

var apiAppName = '${projectShort}-${env}-api'
var webAppName = '${projectShort}-${env}-web'
// Single shared B1 plan for both API and Web (cost-optimised for early staging)
// Scale-up: change SKU in modules/appservice.bicep, or split into two plans
var sharedPlanName = '${projectShort}-${env}-plan'
var dbServerName = '${projectShort}-${env}-db'
var kvName = '${projectShort}-${env}-kv'
var storageName = '${projectShort}${env}storage'
var insightsName = '${projectShort}-${env}-insights'
var logsName = '${projectShort}-${env}-logs'

// ── Role Definition IDs (built-in Azure roles) ────────────────────────────

var kvSecretsOfficerRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
)
var kvSecretsUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
var storageBlobDataContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

// ── Module: Monitoring ─────────────────────────────────────────────────────

module monitoringModule 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    logsName: logsName
    insightsName: insightsName
  }
}

// ── Module: Key Vault ──────────────────────────────────────────────────────
// RBAC assignments added below after managed identities are known

module kvModule 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    kvName: kvName
  }
}

// ── Module: Storage ────────────────────────────────────────────────────────
// RBAC assignments added below after managed identities are known

module storageModule 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    storageName: storageName
  }
}

// ── Module: App Services ───────────────────────────────────────────────────
// API (Python 3.12) + Web (Node 22) — shared B1 plan, system-assigned managed identity
// Key Vault references in app settings activate once RBAC assignments below are applied

module appServiceModule 'modules/appservice.bicep' = {
  name: 'appservice'
  params: {
    location: location
    apiAppName: apiAppName
    webAppName: webAppName
    sharedPlanName: sharedPlanName
    kvUri: kvModule.outputs.kvUri
    appInsightsConnectionString: monitoringModule.outputs.insightsConnectionString
  }
}

// ── Module: PostgreSQL ─────────────────────────────────────────────────────

module postgresModule 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    dbServerName: dbServerName
    dbAdminPassword: dbAdminPassword
  }
}

// ── RBAC Assignments ───────────────────────────────────────────────────────
// Scoped to existing KV/Storage resources after modules deploy them.
// Uses existing references so Bicep can set the correct scope for roleAssignments.

resource kvExisting 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: kvModule.outputs.kvName
}

resource storageExisting 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageModule.outputs.storageAccountName
}

// API managed identity → Key Vault Secrets User
resource apiKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kvExisting.id, appServiceModule.outputs.apiManagedIdentityPrincipalId, kvSecretsUserRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: appServiceModule.outputs.apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Web managed identity → Key Vault Secrets User
resource webKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kvExisting.id, appServiceModule.outputs.webManagedIdentityPrincipalId, kvSecretsUserRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: appServiceModule.outputs.webManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// API managed identity → Storage Blob Data Contributor
resource apiStorageBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageExisting.id, appServiceModule.outputs.apiManagedIdentityPrincipalId, storageBlobDataContributorRoleId)
  scope: storageExisting
  properties: {
    roleDefinitionId: storageBlobDataContributorRoleId
    principalId: appServiceModule.outputs.apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// GitHub Actions SP → Key Vault Secrets Officer (optional — set githubActionsPrincipalId to activate)
resource githubActionsKvOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(githubActionsPrincipalId)) {
  name: guid(kvExisting.id, githubActionsPrincipalId, kvSecretsOfficerRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsOfficerRoleId
    principalId: githubActionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ── Outputs ────────────────────────────────────────────────────────────────

output apiAppName string = apiAppName
output webAppName string = webAppName
output dbServerName string = dbServerName
output kvName string = kvName
output storageName string = storageName
output insightsName string = insightsName
output logsName string = logsName
output apiUrl string = 'https://${appServiceModule.outputs.apiDefaultHostname}'
output webUrl string = 'https://${appServiceModule.outputs.webDefaultHostname}'
output dbFqdn string = postgresModule.outputs.dbServerFqdn
