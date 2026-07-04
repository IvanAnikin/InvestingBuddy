// PostgreSQL Flexible Server 16 — Standard_B1ms (burstable, staging only)
// High availability disabled; backup 7 days; auto-grow storage
// Admin password passed as a secure parameter — stored in Key Vault as 'db-password'

@description('Azure region')
param location string

@description('PostgreSQL Flexible Server name')
param dbServerName string

@description('Database admin username')
param dbAdminUser string = 'ibadmin'

@secure()
@description('Database admin password — generate with: openssl rand -hex 16')
param dbAdminPassword string

@description('Initial database name')
param dbName string = 'investingbuddy'

resource dbServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: dbServerName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: dbAdminUser
    administratorLoginPassword: dbAdminPassword
    storage: {
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: dbServer
  name: dbName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Allow all Azure-internal IP traffic (0.0.0.0 → 0.0.0.0 = "Allow Azure Services")
resource azureServicesRule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: dbServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output dbServerFqdn string = dbServer.properties.fullyQualifiedDomainName
output dbServerName string = dbServer.name
