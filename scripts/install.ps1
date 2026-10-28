param(
    [ValidateSet("cpu", "cuda")]
    [string]$Device,
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "安裝 uv…"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    Update-SessionPath
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv 已安裝但這個視窗還找不到它，請重新開啟 PowerShell 後再執行一次"
    }
}

if (-not $Device) {
    $Device = if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { "cuda" } else { "cpu" }
    Write-Host "偵測到的裝置：$Device（可加 -Device cpu 或 -Device cuda 指定）"
}

$group = @{ cpu = "cpu"; cuda = "cu130" }[$Device]
Write-Host "安裝套件（PyTorch $group）…"
uv sync --frozen --no-default-groups --group $group
if ($LASTEXITCODE -ne 0) {
    throw "安裝套件失敗"
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "已建立 .env，可依需要修改"
}

if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $desktop "Breeze ASR.lnk"))
    $shortcut.TargetPath = (Get-Command powershell.exe).Source
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'start.ps1')`""
    $shortcut.WorkingDirectory = $root
    $shortcut.Save()
    Write-Host "已在桌面建立捷徑「Breeze ASR」"
}

Write-Host "安裝完成。點桌面捷徑或執行 scripts\start.ps1 啟動；第一次啟動會下載並轉換約 9 GB 的模型。"
