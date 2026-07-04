// InvestingBuddy — Azure Infrastructure
// Main Bicep template for all environments.

@description('Short environment label, e.g. stg or prod.')
param environment string = 'stg'

@description('Azure region for most resources.')
param location string = resourceGroup().location

// ---------------------------------------------------------------------------
// Resilience parameters
// ---------------------------------------------------------------------------

@description('Set to true to skip RBAC role assignments. Required for deployments where the identity cannot perform Microsoft.Authorization/roleAssignments/write.')
param skipRbac bool = false

@description('Region override for the PostgreSQL Flexible Server. Defaults to the resource group location.')
param dbLocation string = resourceGroup().location

@description('Override for the PostgreSQL server name. When empty the name is auto-generated.')
param dbServerNameOverride string = ''

// ---------------------------------------------------------------------------
// Derived values
// ---------------------------------------------------------------------------

var dbServerName = dbServerNameOverride != '' ? dbServerNameOverride : 'ib-${environment}-psql'
var appServicePlanName = 'ib-${environment}-asp'
var apiAppName = 'ib-${environment}-api'
var storageAccountName = 'ib${environment}sa'

// ---------------------------------------------------------------------------
// App Service Plan
// ---------------------------------------------------------------------------

resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: appServicePlanName
  location: location
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  properties: {
    reserved: true // Linux
  }
}

// ---------------------------------------------------------------------------
// API App Service
// ---------------------------------------------------------------------------

resource apiApp 'Microsoft.Web/sites@2023-01-01' = {
  name: apiAppName
  location: location
  properties: {
    serverFarmId: appServicePlan.id
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appSettings: [
        {
          name: 'APP_ENV'
          value: environment
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// PostgreSQL Flexible Server
// ---------------------------------------------------------------------------

module postgresql 'modules/postgresql.bicep' = {
  name: 'postgresql'
  params: {
    serverName: dbServerName
    location: dbLocation
    environment: environment
  }
}

// ---------------------------------------------------------------------------
// Storage Account
// ---------------------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
}

// ---------------------------------------------------------------------------
// RBAC role assignments — gated so deployments can skip when the identity
// lacks Microsoft.Authorization/roleAssignments/write permission.
// ---------------------------------------------------------------------------

resource apiAppStorageBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac) {
  name: guid(storageAccount.id, apiApp.id, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: apiApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output apiAppName string = apiApp.name
output dbServerName string = dbServerName
output storageAccountName string = storageAccount.name
