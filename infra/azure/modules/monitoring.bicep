// Log Analytics Workspace + Application Insights
// Used by App Services for monitoring and alerting

@description('Azure region')
param location string

@description('Log Analytics workspace name')
param logsName string

@description('Application Insights resource name')
param insightsName string

resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

output insightsConnectionString string = insights.properties.ConnectionString
output insightsInstrumentationKey string = insights.properties.InstrumentationKey
output logsWorkspaceId string = logs.id
