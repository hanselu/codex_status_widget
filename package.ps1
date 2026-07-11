[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$KeepRunning,
    [switch]$LaunchAfterBuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSCommandPath
Set-Location -LiteralPath $ProjectRoot

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "==> $Name"
    & $Action
}

function Assert-CommandExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少必要命令：$Name"
    }
}

$ExePath = Join-Path $ProjectRoot 'dist\codex_status_widget.exe'

Invoke-Step '检查工具' {
    Assert-CommandExists 'uv'
    uv --version
}

if (-not $SkipTests) {
    Invoke-Step '运行测试' {
        uv run pytest
    }
}

if (-not $KeepRunning) {
    Invoke-Step '停止正在运行的小挂件' {
        $processes = Get-Process -Name 'codex_status_widget' -ErrorAction SilentlyContinue
        if ($processes) {
            $processes | Stop-Process -Force
            Write-Host "已停止 $($processes.Count) 个进程。"
        } else {
            Write-Host '未发现正在运行的小挂件进程。'
        }
    }
}

Invoke-Step '清理构建产物' {
    foreach ($relativePath in @('build', 'dist')) {
        $target = Join-Path $ProjectRoot $relativePath
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force
            Write-Host "已删除 $relativePath"
        }
    }
}

Invoke-Step '打包可执行文件' {
    uv run --with pyinstaller pyinstaller --noconfirm --clean codex_status_widget.spec
}

Invoke-Step '检查输出文件' {
    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "打包命令已结束，但没有找到可执行文件：$ExePath"
    }

    $item = Get-Item -LiteralPath $ExePath
    $hash = Get-FileHash -LiteralPath $ExePath -Algorithm SHA256
    Write-Host "路径：$($item.FullName)"
    Write-Host "大小：$($item.Length) 字节"
    Write-Host "更新时间：$($item.LastWriteTime)"
    Write-Host "SHA256: $($hash.Hash.ToLowerInvariant())"
}

if ($LaunchAfterBuild) {
    Invoke-Step '启动可执行文件' {
        Start-Process -FilePath $ExePath -WorkingDirectory $ProjectRoot
    }
}
