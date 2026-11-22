# vbr-write-mcp demo (Windows): sahte VBR + MCP Inspector. Gerçek bir VBR'a hiçbir şey göndermez.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Sanal ortam kuruluyor (.venv)..." -ForegroundColor Cyan
    py -3 -m venv .venv
}
Write-Host "Paketler yukleniyor..." -ForegroundColor Cyan
& $py -m pip install -q --disable-pip-version-check -e . fastapi uvicorn

Write-Host "Demo VBR baslatiliyor: http://127.0.0.1:9419 (ayri pencere)" -ForegroundColor Cyan
Start-Process -FilePath $py -ArgumentList "demo\demo_vbr.py" -WorkingDirectory $root
Start-Sleep -Seconds 4
Start-Process "http://127.0.0.1:9419/_demo"

$server = Join-Path $root ".venv\Scripts\vbr-write-mcp.exe"
$audit = Join-Path $root "demo\audit.jsonl"
Write-Host "MCP Inspector aciliyor - tarayicida 'Connect' deyin." -ForegroundColor Green
npx -y @modelcontextprotocol/inspector `
    -e VBR_BASE_URL=http://127.0.0.1:9419 -e VBR_USERNAME=demo -e VBR_PASSWORD=demo `
    -e VBRW_CONFIRMATION_MODE=token -e "VBRW_AUDIT_LOG=$audit" -e FASTMCP_SHOW_SERVER_BANNER=false `
    $server
