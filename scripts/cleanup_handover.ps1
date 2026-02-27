$ErrorActionPreference = "SilentlyContinue"

Write-Host "Cleaning runtime/cache artifacts..."

Get-ChildItem "backend/logs" -Force | Remove-Item -Recurse -Force
Get-ChildItem "backend/skool_accounts" -Force | Remove-Item -Recurse -Force
Remove-Item "backend/__pycache__" -Recurse -Force
Remove-Item "frontend/dist" -Recurse -Force
Remove-Item "_engine_diff.txt" -Force

New-Item -ItemType Directory -Path "backend/logs" -Force | Out-Null
New-Item -ItemType Directory -Path "backend/skool_accounts" -Force | Out-Null

Write-Host "Cleanup complete."
