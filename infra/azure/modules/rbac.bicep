// Role assignments for the app-service managed identities.
//
// Extracted into a module because `Microsoft.Authorization/roleAssignments`
// requires its `name` and `scope` to be computable at the START of a
// deployment. In main.bicep the principal IDs are outputs of the App Service
// module (only known at runtime), which makes `guid(...)` uncomputable there
// and fails compilation with BCP120. Module parameters, by contrast, are
// resolved before the module deployment begins, so the same expressions are
// legal here.
//
// Every assignment is guarded by `skipRbac`. The deploying identity needs
// Microsoft.Authorization/roleAssignments/write; when it only has Contributor,
// set skipRbac=true and configure app settings as literal values instead of
// Key Vault references.

@description('Key Vault name to scope the Secrets User assignments to')
param kvName string

@description('Storage account name to scope the Blob Data Contributor assignment to')
param storageName string

@description('API app system-assigned managed identity principal ID')
param apiPrincipalId string

@description('Web app system-assigned managed identity principal ID')
param webPrincipalId string

@description('GitHub Actions App Registration principal ID. Empty disables the Secrets Officer assignment.')
param githubActionsPrincipalId string = ''

@description('Set to true to skip every role assignment in this module.')
param skipRbac bool = false

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

resource kvExisting 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: kvName
}

resource storageExisting 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

// API managed identity → Key Vault Secrets User
resource apiKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac) {
  name: guid(kvExisting.id, apiPrincipalId, kvSecretsUserRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: apiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Web managed identity → Key Vault Secrets User
resource webKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac) {
  name: guid(kvExisting.id, webPrincipalId, kvSecretsUserRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsUserRoleId
    principalId: webPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// API managed identity → Storage Blob Data Contributor
resource apiStorageBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac) {
  name: guid(storageExisting.id, apiPrincipalId, storageBlobDataContributorRoleId)
  scope: storageExisting
  properties: {
    roleDefinitionId: storageBlobDataContributorRoleId
    principalId: apiPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// GitHub Actions SP → Key Vault Secrets Officer (optional)
resource githubActionsKvOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!skipRbac && !empty(githubActionsPrincipalId)) {
  name: guid(kvExisting.id, githubActionsPrincipalId, kvSecretsOfficerRoleId)
  scope: kvExisting
  properties: {
    roleDefinitionId: kvSecretsOfficerRoleId
    principalId: githubActionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}
