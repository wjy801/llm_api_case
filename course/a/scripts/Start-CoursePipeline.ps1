[CmdletBinding()]
param(
    [ValidateRange(1, 26)]
    [int]$FromLesson = 1,

    [ValidateRange(1, 26)]
    [int]$ToLesson = 26,

    [string]$Model = "",

    [ValidateSet("low", "medium", "high", "xhigh", "max", "ultra")]
    [string]$ReasoningEffort = "",

    [ValidateRange(-1, 10)]
    [int]$MaxRevisionRounds = -1,

    [switch]$Force,
    [switch]$StopOnFailure,
    [switch]$SkipFinalAudit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Quote-PowerShellLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

$courseRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtimeRoot = Join-Path $courseRoot ".course-automation\runtime"
$pipelinePath = Join-Path $PSScriptRoot "Invoke-CoursePipeline.ps1"
$pidPath = Join-Path $runtimeRoot "pipeline.pid"
$stdoutPath = Join-Path $runtimeRoot "background.out.log"
$stderrPath = Join-Path $runtimeRoot "background.err.log"

if (-not (Test-Path -LiteralPath $runtimeRoot)) {
    New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
}

if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
    $rawPid = (Get-Content -Raw -Encoding UTF8 -LiteralPath $pidPath).Trim()
    if (($rawPid -match "^\d+$") -and ($null -ne (Get-Process -Id ([int]$rawPid) -ErrorAction SilentlyContinue))) {
        throw "课程流水线已经在运行，PID：$rawPid"
    }
}

$parts = New-Object System.Collections.Generic.List[string]
$parts.Add("& " + (Quote-PowerShellLiteral -Value $pipelinePath))
$parts.Add("-FromLesson $FromLesson")
$parts.Add("-ToLesson $ToLesson")
$parts.Add("-MaxRevisionRounds $MaxRevisionRounds")
if ($Model) { $parts.Add("-Model " + (Quote-PowerShellLiteral -Value $Model)) }
if ($ReasoningEffort) { $parts.Add("-ReasoningEffort " + (Quote-PowerShellLiteral -Value $ReasoningEffort)) }
if ($Force) { $parts.Add("-Force") }
if ($StopOnFailure) { $parts.Add("-StopOnFailure") }
if ($SkipFinalAudit) { $parts.Add("-SkipFinalAudit") }

$command = $parts -join " "
$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
$hostExecutable = (Get-Process -Id $PID).Path
$process = Start-Process `
    -FilePath $hostExecutable `
    -ArgumentList @("-NoProfile", "-EncodedCommand", $encodedCommand) `
    -WorkingDirectory $courseRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

[System.IO.File]::WriteAllText($pidPath, [string]$process.Id, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "课程流水线已在后台启动，PID：$($process.Id)" -ForegroundColor Green
Write-Host "状态：.\scripts\Get-CourseStatus.ps1"
Write-Host "标准输出：$stdoutPath"
Write-Host "错误输出：$stderrPath"
