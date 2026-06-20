// InvestingBuddy — Azure Infrastructure (Staging)
// Status: PLACEHOLDER — not ready for deployment
// Complete infra/azure/README.md checklist before implementing this file.
//
// Planned modules (implement after approval):
//   modules/monitoring.bicep   — Log Analytics + Application Insights
//   modules/keyvault.bicep     — Key Vault + RBAC assignments
//   modules/storage.bicep      — Storage Account + container
//   modules/postgres.bicep     — PostgreSQL Flexible Server
//   modules/appservice.bicep   — App Service Plan + App Service (API + Web)

targetScope = 'resourceGroup'

@description('Environment name (stg or prod)')
param env string = 'stg'

@description('Azure region for all resources')
param location string = resourceGroup().location

@description('Project short name used in resource naming')
param projectShort string = 'ib'

// Resource name outputs (for reference)
var apiAppName = '${projectShort}-${env}-api'
var webAppName = '${projectShort}-${env}-web'
var dbServerName = '${projectShort}-${env}-db'
var kvName = '${projectShort}-${env}-kv'
var storageName = '${projectShort}${env}storage'
var insightsName = '${projectShort}-${env}-insights'
var logsName = '${projectShort}-${env}-logs'

output apiAppName string = apiAppName
output webAppName string = webAppName
output dbServerName string = dbServerName
output kvName string = kvName
output storageName string = storageName
output insightsName string = insightsName
output logsName string = logsName
