// Storage Account (StorageV2, LRS) + private blob container
// RBAC assignments are handled in main.bicep after managed identities are created
// Public blob access is disabled — the API reads via managed identity

@description('Azure region')
param location string

@description('Storage account name (no hyphens — Azure limit)')
param storageName string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'investingbuddy-documents'
  properties: {
    publicAccess: 'None'
  }
}

output storageAccountName string = storage.name
output storageId string = storage.id
