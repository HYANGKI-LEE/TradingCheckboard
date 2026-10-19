<#
RawData_master.xlsx 를 인포맥스 IMDH 로 재조회해서 저장한다 (daily_update.py 가 호출).

원본은 건드리지 않는다: -Master 를 -Out(임시 복사본)으로 복사해서 거기서만 작업.
  1. 복사본을 Excel(COM, 숨김)로 열고 -StartDate 가 있으면 모든 시트의 시작일(B1)을 바꾼다
  2. 인포맥스 XLL 등록 + IMxl_OnRefreshData(히스토리 재조회) 실행
  3. 모든 시트의 "마지막 날짜" 서명이 -StableSec 동안 변하지 않을 때까지 대기 (최대 -MaxWaitSec)
  4. 저장하고 Excel 종료.  성공 0 / 실패 1
#>
param(
    [Parameter(Mandatory = $true)][string]$Master,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$StartDate = "",
    [int]$MinWaitSec = 90,
    [int]$StableSec = 90,
    [int]$MaxWaitSec = 1500
)

$ErrorActionPreference = "Stop"
$XllPath = "C:\Infomax\bin\excel64\imxlexcelai64.xll"

function Log($m) { Write-Output "$(Get-Date -Format 'HH:mm:ss') [excel] $m" }

function Get-Signature($wb) {
    $parts = @()
    foreach ($ws in $wb.Sheets) {
        $lastRow = $ws.Cells(1, 1).End(4).Row
        $parts += "$($ws.Name)=$lastRow/$($ws.Cells($lastRow, 1).Value2)"
    }
    return ($parts -join "|")
}

Copy-Item -Path $Master -Destination $Out -Force
$preIds = @(Get-Process -Name EXCEL -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$excel = $null
$wb = $null
$ok = $false
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $wb = $excel.Workbooks.Open($Out)
    Start-Sleep -Seconds 5
    if ($wb.ReadOnly) { throw "읽기 전용으로 열림" }
    $reg = $excel.RegisterXLL($XllPath)
    Log "RegisterXLL => $reg"

    if ($StartDate) {
        $d = [datetime]::ParseExact($StartDate, "yyyy-MM-dd", $null)
        foreach ($ws in $wb.Sheets) { $ws.Range("B1").Value2 = $d.ToOADate() }
        Log "시작일(B1) -> $StartDate (전 시트)"
    }

    $excel.Run("IMxl_OnRefreshData")
    Log "재조회 매크로 실행, 완료 대기"
    $start = Get-Date
    $lastSig = ""
    $lastChange = Get-Date
    while ($true) {
        Start-Sleep -Seconds 15
        $elapsed = ((Get-Date) - $start).TotalSeconds
        $sig = $null
        try { $sig = Get-Signature $wb } catch { }
        if ($null -eq $sig) {
            $lastChange = Get-Date     # Excel 이 처리 중이라 호출 거부 - 아직 진행 중으로 본다
        } elseif ($sig -ne $lastSig) {
            $lastSig = $sig
            $lastChange = Get-Date
        }
        $stable = ((Get-Date) - $lastChange).TotalSeconds
        if ($elapsed -ge $MinWaitSec -and $stable -ge $StableSec -and $null -ne $sig) { Log "변화 없음 ${StableSec}초 - 완료 (경과 $([int]$elapsed)초)"; break }
        if ($elapsed -ge $MaxWaitSec) { Log "최대 대기시간 초과 (${MaxWaitSec}초)"; break }
    }

    $saved = $false
    for ($t = 1; $t -le 20 -and -not $saved; $t++) {
        try { $wb.Save(); $saved = $true } catch { Log "저장 재시도 $t/20: $($_.Exception.Message)"; Start-Sleep -Seconds 15 }
    }
    if (-not $saved) { throw "저장 실패" }
    Log "저장 완료"
    $ok = $true
} catch {
    Log "ERROR: $($_.Exception.Message)"
} finally {
    try { if ($wb) { $wb.Close($false) } } catch { }
    try { if ($excel) { $excel.Quit() } } catch { }
    Start-Sleep -Seconds 2
    # 우리가 띄운 Excel 만 정리 (사용자가 쓰던 Excel 은 건드리지 않음)
    Get-Process -Name EXCEL -ErrorAction SilentlyContinue | Where-Object { $preIds -notcontains $_.Id } |
        ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
}
if ($ok) { exit 0 } else { exit 1 }
