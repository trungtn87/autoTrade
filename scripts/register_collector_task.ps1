<#
.SYNOPSIS
Đăng ký hoặc hủy tác vụ Windows Task Scheduler cho bộ thu dữ liệu BingX V3-17c.

.DESCRIPTION
Tạo tác vụ chạy mỗi giờ vào phút :20 (sau bộ thu :07 trên VPS),
kèm kích hoạt khi đăng nhập và tự động chạy bù nếu lỡ lịch (-StartWhenAvailable).
#>
[CmdletBinding()]
param(
    [switch]$Unregister,
    [string]$TaskName = "AutoTradeV3-BingXCollector",
    [string]$RepoRoot = "D:\AutoTrade",
    [string]$DataDir = "D:\AutoTrade\data\bingx"
)

$ErrorActionPreference = "Stop"

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        Write-Host "Không tìm thấy tác vụ '$TaskName' để hủy."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Đã hủy tác vụ '$TaskName' thành công."
    exit 0
}

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $pythonCmd = (Get-Command python -ErrorAction Stop).Source
} else {
    $pythonCmd = $venvPython
}

$arguments = "-m autotrade_v3.collector_v3 run --data-dir `"$DataDir`""
$action = New-ScheduledTaskAction `
    -Execute $pythonCmd `
    -Argument $arguments `
    -WorkingDirectory $RepoRoot

# Tính mốc :20 kế tiếp làm điểm bắt đầu lặp mỗi giờ
$now = Get-Date
$nextRun = Get-Date -Year $now.Year -Month $now.Month -Day $now.Day -Hour $now.Hour -Minute 20 -Second 0
if ($nextRun -le $now) {
    $nextRun = $nextRun.AddHours(1)
}

$hourlyTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At $nextRun `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($hourlyTrigger, $logonTrigger) `
    -Settings $settings `
    -Principal $principal `
    -Description "Bộ thu dữ liệu BingX & Supabase chỉ-đọc cục bộ (V3-17c) - chạy :20 mỗi giờ và khi đăng nhập." `
    -Force | Out-Null

Write-Host "Đã đăng ký tác vụ '$TaskName' thành công:"
Write-Host "  - Lệnh: $pythonCmd $arguments"
Write-Host "  - Lịch: mỗi giờ tại phút :20 (bắt đầu từ $($nextRun.ToString('yyyy-MM-dd HH:mm:ss'))) + khi đăng nhập"
Write-Host "  - Chạy bù khi lỡ lịch: BẬT (StartWhenAvailable)"
Write-Host "  - Nhật ký xoay vòng: $DataDir\logs\collector.log"
