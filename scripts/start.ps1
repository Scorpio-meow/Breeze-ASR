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
$url = uv run --no-sync --env-file .env python -c "from app.client import service_url; print(service_url(None))"
if ($LASTEXITCODE -ne 0) {
    throw "無法從 .env 的 ASR_HOST、ASR_PORT 組出服務網址"
}

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

$request = Join-Path $root ".update\request.json"
while ($true) {
    Write-Host "ListenMiao：$url（關閉這個視窗即停止服務）"
    uv run --no-sync --env-file .env python -m app --updatable
    $code = $LASTEXITCODE
    if (-not (Test-Path $request)) {
        break
    }
    Write-Host ""
    Write-Host "下載並套用新版…"
    uv run --no-sync python -m app.update --root . apply --yes
    if ($LASTEXITCODE -eq 0) {
        $group = uv run --no-sync python -m app.update --root . group
        if ($LASTEXITCODE -eq 0) {
            uv sync --frozen --no-default-groups --group $group
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "安裝新版的套件失敗，還原成更新前的檔案"
            uv run --no-sync python -m app.update --root . rollback
            $group = uv run --no-sync python -m app.update --root . group
            uv sync --frozen --no-default-groups --group $group
        }
    } else {
        Write-Warning "更新沒有完成，繼續用原本的版本"
    }
    uv run --no-sync python -m app.envfile .env.example .env
    Write-Host ""
}
if ($code -ne 0) {
    Wait-BeforeClose "服務異常結束（結束代碼 $code）。原因見上方訊息，或 $(Join-Path $settings["ASR_DATA_DIR"] 'logs') 裡的日誌。"
    exit $code
}
