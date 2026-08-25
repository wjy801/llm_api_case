[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$courseRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtimeRoot = Join-Path $courseRoot ".course-automation\runtime"
$statePath = Join-Path $runtimeRoot "state.json"
$pidPath = Join-Path $runtimeRoot "pipeline.pid"

$running = $false
$processId = $null
if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
    $rawPid = (Get-Content -Raw -Encoding UTF8 -LiteralPath $pidPath).Trim()
    if ($rawPid -match "^\d+$") {
        $processId = [int]$rawPid
        $running = $null -ne (Get-Process -Id $processId -ErrorAction SilentlyContinue)
    }
}

Write-Host "课程流水线进程：$(if ($running) { "运行中（PID $processId）" } else { "未运行" })"

if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    Write-Host "尚未创建状态文件。先运行 Invoke-CoursePipeline.ps1。"
    return
}

$state = Get-Content -Raw -Encoding UTF8 -LiteralPath $statePath | ConvertFrom-Json
$state.lessons |
    Sort-Object { [int]$_.id } |
    Select-Object id, title, status, score, revision_rounds, updated_at |
    Format-Table -AutoSize

$groups = $state.lessons | Group-Object status | Sort-Object Name
Write-Host "状态汇总："
$groups | ForEach-Object { Write-Host "  $($_.Name): $($_.Count)" }

$summaryPath = Join-Path $runtimeRoot "summary.md"
if (Test-Path -LiteralPath $summaryPath -PathType Leaf) {
    Write-Host "摘要：$summaryPath"
}
