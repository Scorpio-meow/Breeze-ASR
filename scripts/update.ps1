$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Select-String -Path pyproject.toml -Pattern '^name = "(listenmiao|breeze-asr-webapp)"$' -Quiet)) {
    throw "找不到 ListenMiao：這支腳本要放在 ListenMiao 資料夾的 scripts 裡執行"
}
$homepage = Select-String -Path pyproject.toml -Pattern '^Homepage = "(.+)"$'
if (-not $homepage) {
    throw "這個版本的 pyproject.toml 沒有記錄下載位置（0.3.0 以前的版本），請到 GitHub 的 Releases 頁面手動下載新版"
}
New-Item -ItemType Directory -Force .update | Out-Null
$updater = Join-Path $root ".update\update.py"
Write-Host "下載最新的更新程式…"
Invoke-WebRequest "$($homepage.Matches[0].Groups[1].Value)/releases/latest/download/update.py" -OutFile $updater -UseBasicParsing

uv run --no-sync python $updater --root . apply
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
$group = uv run --no-sync python $updater --root . group
if ($LASTEXITCODE -eq 0) {
    uv sync --frozen --no-default-groups --group $group
}
if ($LASTEXITCODE -ne 0) {
    Write-Warning "安裝新版的套件失敗，還原成更新前的檔案"
    uv run --no-sync python $updater --root . rollback
    $group = uv run --no-sync python $updater --root . group
    uv sync --frozen --no-default-groups --group $group
    exit 1
}
uv run --no-sync python -m app.envfile .env.example .env
Write-Host "完成。點桌面捷徑或執行 scripts\start.ps1 啟動。"
