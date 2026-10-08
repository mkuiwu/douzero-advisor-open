param(
    [Parameter(Mandatory = $true)][string]$LauncherPath,
    [string[]]$ForwardArguments = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 通过 cmd /c call 保留批处理自身的参数和返回码；UAC 同意后才执行 GUI 启动器。
$arguments = @("/d", "/c", "call", ('"{0}"' -f $LauncherPath), "--elevated") + $ForwardArguments
$process = Start-Process -FilePath $env:ComSpec -Verb RunAs -ArgumentList $arguments -Wait -PassThru
exit $process.ExitCode
