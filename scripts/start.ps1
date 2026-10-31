param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Wait-BeforeClose([string]$message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Read-Host "按 Enter 關閉視窗" | Out-Null
}

trap {
    Wait-BeforeClose "啟動失敗：$_"
    exit 1
}

if (-not (Test-Path .env)) {
    throw "找不到 .env，請先執行 scripts\install.ps1"
}

uv run --no-sync python -m app.envfile .env.example .env
if ($LASTEXITCODE -ne 0) {
    Write-Warning "無法檢查 .env 是否缺少設定，照原本的 .env 繼續啟動"
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
if ($LASTEXITCODE -ne 0) {
    Wait-BeforeClose "服務異常結束（結束代碼 $LASTEXITCODE）。原因見上方訊息，或 $(Join-Path $settings["ASR_DATA_DIR"] 'logs') 裡的日誌。"
    exit $LASTEXITCODE
}
