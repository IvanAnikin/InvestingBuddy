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

@description('Set to true to skip RBAC role assignments. Required for deployments where the identity cannot perform Microsoft.Authorization/roleAssignments/write.')
param skipRbac bool = false

@description('Region override for the PostgreSQL Flexible Server. Defaults to the resource group location.')
param dbLocation string = resourceGroup().location

@description('Override for the PostgreSQL server name. When empty the name is auto-generated.')
param dbServerNameOverride string = ''

@description('Set to true to include the Document Intelligence (OCR) module. Defaults false — see the WARNING below before enabling.')
param deployDocumentIntelligence bool = false

@description('Document Intelligence SKU — F0 (Free, one per subscription) or S0 (Standard)')
@allowed([
  'F0'
  'S0'
])
param documentIntelligenceSku string = 'F0'

// ── Resource Names ─────────────────────────────────────────────────────────

var apiAppName = '${projectShort}-${env}-api'
var webAppName = '${projectShort}-${env}-web'
// Single shared B1 plan for both API and Web (cost-optimised for early staging)
// Scale-up: change SKU in modules/appservice.bicep, or split into two plans
var sharedPlanName = '${projectShort}-${env}-plan'
var dbServerName = dbServerNameOverride != '' ? dbServerNameOverride : '${projectShort}-${env}-db'
var kvName = '${projectShort}-${env}-kv'
var storageName = '${projectShort}${env}storage'
var insightsName = '${projectShort}-${env}-insights'
var logsName = '${projectShort}-${env}-logs'
var docIntelName = '${projectShort}-${env}-docintel'

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

// ── Module: Document Intelligence (OCR, Phase 32A Slice 5B.2) ─────────────
// OFF by default (deployDocumentIntelligence=false). This module is provided
// for reproducibility of a from-scratch environment. WARNING: this file's
// appServiceModule.appSettings array is NOT authoritative for the live app's
// current settings (many flags, incl. PRIMARY_DOCUMENT_OCR_ENABLED /
// AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT, were added out-of-band via
// `az webapp config appsettings set`, which merges). Re-running
// `az deployment group create` against this WHOLE main.bicep file would
// replace the API app's appSettings collection with only what's listed in
// appservice.bicep, dropping every out-of-band flag. The Document
// Intelligence resource itself was provisioned via a standalone, isolated
// deployment scoped to ONLY modules/documentintelligence.bicep — never via a
// full main.bicep apply — specifically to avoid this risk. Do not run a full
// main.bicep deployment against ib-stg-rg without first reconciling
// appservice.bicep's appSettings array against the live `az webapp config
// appsettings list` output.

module docIntelModule 'modules/documentintelligence.bicep' = if (deployDocumentIntelligence) {
  name: 'documentintelligence'
  params: {
    location: location
    accountName: docIntelName
    skuName: documentIntelligenceSku
    apiManagedIdentityPrincipalId: appServiceModule.outputs.apiManagedIdentityPrincipalId
    skipRbac: skipRbac
  }
}

// ── Module: PostgreSQL ─────────────────────────────────────────────────────

module postgresModule 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: dbLocation
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
resource apiKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac) {
  name: guid(kvExisting.id, appServiceModule.outputs.apiManagedIdentityPrincipalId, kvSecretsUserRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: appServiceModule.outputs.apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Web managed identity → Key Vault Secrets User
resource webKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac) {
  name: guid(kvExisting.id, appServiceModule.outputs.webManagedIdentityPrincipalId, kvSecretsUserRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: appServiceModule.outputs.webManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// API managed identity → Storage Blob Data Contributor
resource apiStorageBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac) {
  name: guid(storageExisting.id, appServiceModule.outputs.apiManagedIdentityPrincipalId, storageBlobDataContributorRoleId)
  scope: storageExisting
  properties: {
    roleDefinitionId: storageBlobDataContributorRoleId
    principalId: appServiceModule.outputs.apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// GitHub Actions SP → Key Vault Secrets Officer (optional — set githubActionsPrincipalId to activate)
resource githubActionsKvOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac && !empty(githubActionsPrincipalId)) {
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
