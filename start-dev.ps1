$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendCommand = @'
Set-Location -LiteralPath "{0}"
$venvPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {{
  Write-Host "Using existing .venv for backend startup..." -ForegroundColor Green
  & $venvPython -m uvicorn backend.app.main:app --reload
}} else {{
  Write-Host "No existing .venv found. Creating backend environment with uv..." -ForegroundColor Yellow
  uv sync
  if ($LASTEXITCODE -ne 0) {{
    Write-Host ""
    Write-Host "Backend dependency sync failed." -ForegroundColor Red
    Pause
    exit $LASTEXITCODE
  }}
  uv run uvicorn backend.app.main:app --reload
}}
if ($LASTEXITCODE -ne 0) {{
  Write-Host ""
  Write-Host "Backend process exited unexpectedly." -ForegroundColor Red
  Pause
  exit $LASTEXITCODE
}}
'@ -f $projectRoot

$frontendCommand = @'
Set-Location -LiteralPath "{0}\frontend"
npm install
if ($LASTEXITCODE -ne 0) {{
  Write-Host ""
  Write-Host "Frontend dependency install failed." -ForegroundColor Red
  Pause
  exit $LASTEXITCODE
}}
npm run dev
if ($LASTEXITCODE -ne 0) {{
  Write-Host ""
  Write-Host "Frontend process exited unexpectedly." -ForegroundColor Red
  Pause
  exit $LASTEXITCODE
}}
'@ -f $projectRoot

foreach ($command in @("uv", "npm")) {
  if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "Required command '$command' was not found in PATH."
  }
}

Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-ExecutionPolicy", "Bypass",
  "-Command", $backendCommand
) -WorkingDirectory $projectRoot

Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-ExecutionPolicy", "Bypass",
  "-Command", $frontendCommand
) -WorkingDirectory $projectRoot

Write-Host "Backend and frontend are starting in separate windows..." -ForegroundColor Green
Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend: http://127.0.0.1:5173"
