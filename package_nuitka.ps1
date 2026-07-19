[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Force,
    [switch]$LaunchAfterBuild,
    [string]$OutputDir = 'dist_nuitka',
    [string]$OutputName = 'codex_status_widget_nuitka.exe'
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

    Write-Host ''
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

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Action
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Action 失败，退出码：$LASTEXITCODE"
    }
}

function Resolve-ProjectChildPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
    }

    $rootWithSeparator = $resolvedRoot.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "输出目录必须位于项目目录内：$candidate"
    }

    return $candidate
}

$OutputRoot = Resolve-ProjectChildPath $OutputDir
$ExePath = Join-Path $OutputRoot $OutputName
$IconSvgPath = Join-Path $ProjectRoot 'icon.svg'
$IconIcoPath = Join-Path $OutputRoot 'codex_status_widget.ico'
$HookWriterPath = Join-Path $ProjectRoot 'codex_widget\hook_writer.py'

Invoke-Step '检查工具和输入文件' {
    Assert-CommandExists 'uv'
    uv --version
    Assert-NativeSuccess 'uv --version'

    if (-not (Test-Path -LiteralPath $IconSvgPath)) {
        throw "找不到图标文件：$IconSvgPath"
    }
    if (-not (Test-Path -LiteralPath $HookWriterPath)) {
        throw "找不到 hook writer：$HookWriterPath"
    }
}

if (-not $SkipTests) {
    Invoke-Step '运行测试' {
        uv run pytest
        Assert-NativeSuccess 'uv run pytest'
    }
}

Invoke-Step '检查输出目录' {
    if ((Test-Path -LiteralPath $ExePath) -and (-not $Force)) {
        throw "输出文件已存在：$ExePath。若要覆盖 Nuitka 产物，请添加 -Force；或使用 -OutputDir 指定新目录。"
    }

    if ((Test-Path -LiteralPath $OutputRoot) -and $Force) {
        Remove-Item -LiteralPath $OutputRoot -Recurse -Force
        Write-Host "已删除旧 Nuitka 输出目录：$OutputRoot"
    }

    New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
}

Invoke-Step '生成 Windows 图标' {
    $script = @'
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

svg_path = Path(sys.argv[1])
ico_path = Path(sys.argv[2])

app = QApplication.instance() or QApplication([])
renderer = QSvgRenderer(str(svg_path))
if not renderer.isValid():
    raise SystemExit(f"SVG 图标无效：{svg_path}")

image = QImage(QSize(256, 256), QImage.Format_ARGB32)
image.fill(Qt.transparent)

painter = QPainter(image)
try:
    renderer.render(painter)
finally:
    painter.end()

ico_path.parent.mkdir(parents=True, exist_ok=True)
if not image.save(str(ico_path), "ICO"):
    raise SystemExit(f"ICO 图标生成失败：{ico_path}")
'@

    $script | uv run python - $IconSvgPath $IconIcoPath
    Assert-NativeSuccess '生成 Windows 图标'

    if (-not (Test-Path -LiteralPath $IconIcoPath)) {
        throw "图标生成命令已结束，但没有找到 ICO 文件：$IconIcoPath"
    }
    Write-Host "图标：$IconIcoPath"
}

Invoke-Step 'Nuitka 打包可执行文件' {
    $nuitkaArgs = @(
        '-m',
        'nuitka',
        '--assume-yes-for-downloads',
        '--mode=onefile',
        '--enable-plugins=pyside6',
        '--windows-console-mode=disable',
        "--windows-icon-from-ico=$IconIcoPath",
        "--output-dir=$OutputRoot",
        "--output-filename=$OutputName",
        '--include-data-files=codex_widget\hook_writer.py=codex_widget\hook_writer.py',
        'main.py'
    )

    uv run python @nuitkaArgs
    Assert-NativeSuccess 'Nuitka 打包'
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
