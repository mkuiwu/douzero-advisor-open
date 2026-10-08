param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 运行目录只允许位于仓库 logs\runs 下；保留策略绝不扫描或删除其他位置。
$runsRoot = Join-Path $RepositoryRoot "logs\runs"
New-Item -ItemType Directory -Force -Path $runsRoot | Out-Null
$runsRoot = (Resolve-Path -LiteralPath $runsRoot).Path

$runId = "run-" + (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss.fff")
$runDirectory = Join-Path $runsRoot $runId
$suffix = 1
while (Test-Path -LiteralPath $runDirectory) {
    $suffix++
    $runDirectory = Join-Path $runsRoot ("{0}-{1}" -f $runId, $suffix)
}
New-Item -ItemType Directory -Path $runDirectory | Out-Null

# 使用启动 ID 排序而非最后写入时间，运行中的旧批次写入新帧不会改变保留顺序。
$expiredRuns = @(Get-ChildItem -LiteralPath $runsRoot -Directory | Where-Object { $_.Name -like "run-*" } | Sort-Object Name -Descending | Select-Object -Skip 2)
foreach ($expiredRun in $expiredRuns) {
    Remove-Item -LiteralPath $expiredRun.FullName -Recurse -Force
}

# 仅输出新目录，供 cmd 启动器写入子进程环境变量。
Write-Output $runDirectory
