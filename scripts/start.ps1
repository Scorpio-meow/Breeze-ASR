param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path .env)) {
    throw "找不到 .env，請先執行 scripts\install.ps1"
}

$settings = @{}
foreach ($line in Get-Content .env -Encoding UTF8) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        $settings[$Matches[1]] = $Matches[2]
    }
}
$address = if ($settings["ASR_HOST"] -eq "0.0.0.0") { "127.0.0.1" } else { $settings["ASR_HOST"] }
$url = "http://${address}:$($settings["ASR_PORT"])"

if (-not $NoBrowser) {
    Start-Job -ArgumentList $url -ScriptBlock {
        param($url)
        while ($true) {
            try {
                Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
                Start-Process $url
                return
            } catch {
                Start-Sleep -Seconds 1
            }
        }
    } | Out-Null
}

Write-Host "Breeze ASR：$url（關閉這個視窗即停止服務）"
uv run --no-sync --env-file .env python -m app
