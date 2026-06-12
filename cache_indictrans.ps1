$ErrorActionPreference = "Stop"

$vendor = Join-Path $PSScriptRoot "indictrans_service\vendor"
if (Test-Path $vendor) {
  $env:PYTHONPATH = "$vendor;$env:PYTHONPATH"
}

python indictrans_service\cache_models.py
