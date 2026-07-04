using '../main.bicep'

param environment = 'stg'
param location = 'northeurope'
param skipRbac = true
param dbLocation = 'northeurope'
param dbServerNameOverride = 'ib-stg-psql'
