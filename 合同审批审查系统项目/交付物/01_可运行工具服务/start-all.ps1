$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path

$review = Join-Path $root "contract-review-service"
$ai = Join-Path $root "ai-service"
$web = Join-Path $root "frontend"
$python = Join-Path $ai ".venv\Scripts\python.exe"

Write-Host "Starting Contract Review Service on 8001..."
Start-Process powershell -WindowStyle Hidden -ArgumentList @(
  "-NoExit", "-Command",
  "cd '$review'; & '$python' -m uvicorn contract_review_service.main:app --host 127.0.0.1 --port 8001"
)

Write-Host "Starting Contract Review AI Service on 8002..."
Start-Process powershell -WindowStyle Hidden -ArgumentList @(
  "-NoExit", "-Command",
  "cd '$ai'; & '$python' -m contract_review.main"
)

Write-Host "Starting admin frontend on 5174..."
Start-Process powershell -WindowStyle Hidden -ArgumentList @(
  "-NoExit", "-Command",
  "cd '$web'; npm run dev"
)

Write-Host "Done. Open http://127.0.0.1:5174"
