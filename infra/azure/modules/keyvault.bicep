// Key Vault — Standard SKU, RBAC permission model, soft delete enabled
// RBAC assignments are handled in main.bicep after managed identities are created

@description('Azure region')
param location string

@description('Key Vault name (must be globally unique)')
param kvName string

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

output kvUri string = kv.properties.vaultUri
output kvName string = kv.name
output kvId string = kv.id
