$ErrorActionPreference = "Stop"

$env:PYTHONIOENCODING = "utf-8"

$vendor = Join-Path $PSScriptRoot "indictrans_service\vendor"
if (Test-Path $vendor) {
  $env:PYTHONPATH = "$vendor;$env:PYTHONPATH"
}

python -m uvicorn indictrans_service.main:app --host 127.0.0.1 --port 9000
