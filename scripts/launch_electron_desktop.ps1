param(
    [switch]$SkipBuild,
    [switch]$WithTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopRoot = Join-Path $repositoryRoot "desktop-ui"
$electronMain = Join-Path $desktopRoot "out\main\index.js"
$launcherExitCode = 0

function Assert-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少 Electron 桌面启动所需命令：$Name"
    }
}

try {
    Assert-Command "node.exe"
    Assert-Command "npm.cmd"
    Push-Location $desktopRoot
    try {
        if (-not (Test-Path "node_modules\electron\package.json" -PathType Leaf)) {
            Write-Host "[INFO] 正在安装 Electron 桌面依赖..."
            & npm.cmd install
            if ($LASTEXITCODE -ne 0) {
                throw "Electron 依赖安装失败，退出码 $LASTEXITCODE"
            }
        }
        if ($WithTests) {
            Write-Host "[INFO] 正在执行 Electron 桌面测试..."
            & npm.cmd test
            if ($LASTEXITCODE -ne 0) {
                throw "Electron 桌面测试失败，退出码 $LASTEXITCODE"
            }
        }
        if (-not $SkipBuild) {
            Write-Host "[INFO] 正在构建 Electron 桌面界面..."
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) {
                throw "Electron 桌面构建失败，退出码 $LASTEXITCODE"
            }
        }
        elseif (-not (Test-Path $electronMain -PathType Leaf)) {
            throw "--SkipBuild 要求现有 Electron 生产构建：$electronMain"
        }

        # 只启动 Electron。Java Runtime 与 Python 服务由界面内的受控按钮按需创建。
        $env:DOUZERO_REPO_ROOT = $repositoryRoot
        Write-Host "[INFO] 正在启动 Electron；Java 与 Python 服务将在界面中按需启动..."
        & npm.cmd run start
        $electronExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($electronExitCode -ne 0) {
        throw "Electron 桌面进程退出码为 $electronExitCode"
    }
}
catch {
    [Console]::Error.WriteLine("[ERROR] $($_.Exception.Message)")
    $launcherExitCode = 1
}

exit $launcherExitCode
