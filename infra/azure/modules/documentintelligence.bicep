// Azure AI Document Intelligence (Cognitive Services `FormRecognizer` kind) —
// real OCR/layout provider for Phase 32A Slice 5B.2 (ADR-016).
//
// F0 (Free) tier: one per subscription, per-kind. Bounded quota (pages/month +
// requests/min) — adequate for bounded staging validation, not production load.
// No key-based secrets are emitted from this module; the API app authenticates
// via its existing system-assigned managed identity (Cognitive Services User
// role, assigned in main.bicep) using DefaultAzureCredential. Local-auth (keys)
// stays enabled at the resource level only as an SDK fallback the app code does
// not use by default — see ocr_provider.py.

@description('Azure region — must support the FormRecognizer kind')
param location string

@description('Document Intelligence account name (must be globally unique)')
param accountName string

@description('SKU name — F0 (Free, one per subscription) or S0 (Standard)')
@allowed([
  'F0'
  'S0'
])
param skuName string = 'F0'

@description('Principal ID of the API app managed identity — granted Cognitive Services User on this resource. Empty skips the role assignment.')
param apiManagedIdentityPrincipalId string = ''

@description('Set to true to skip RBAC role assignment (mirrors main.bicep skipRbac).')
param skipRbac bool = false

var cognitiveServicesUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'a97b65f3-24c7-4388-baec-2e87135dc908'
)

resource docIntel 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: accountName
  location: location
  kind: 'FormRecognizer'
  sku: {
    name: skuName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: accountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

resource apiDocIntelUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac && !empty(apiManagedIdentityPrincipalId)) {
  name: guid(docIntel.id, apiManagedIdentityPrincipalId, cognitiveServicesUserRoleId)
  scope: docIntel
  properties: {
    roleDefinitionId: cognitiveServicesUserRoleId
    principalId: apiManagedIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

output accountName string = docIntel.name
output endpoint string = docIntel.properties.endpoint
output principalId string = docIntel.identity.principalId
