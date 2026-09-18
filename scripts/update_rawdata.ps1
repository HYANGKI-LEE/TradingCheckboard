<#
Infomax RawData.xlsx 자동 업데이트 스크립트.

매일 새벽에 Windows 작업 스케줄러가 이 스크립트를 실행한다(컴퓨터는 완전 종료가 아니라
절전모드로만 둬야 함 - 작업 스케줄러의 "절전모드에서 깨우기" 옵션으로 기동됨. 또한
Infomax 백그라운드 서비스(InfomaxMain.exe 등)가 로그인된 상태로 떠 있어야 실제 데이터가
갱신된다 - Excel 자체가 켜져 있을 필요는 없음, 별도로 확인됨).

동작 순서:
  1. RawData.xlsx를 임시 경로로 복사 (원본 보호 - refresh 도중 문제가 생겨도 원본은 안전)
  2. 그 복사본을 Excel(COM)으로 열고 Infomax 애드인의 히스토리 재조회 매크로 실행
     (IMxl_OnRefreshData - 리본 로드 시점에만 연결되는 구조라 XLL을 직접 RegisterXLL로 로드)
  3. 재계산 대기 후 저장
  4. 각 시트의 최신 날짜를 원본과 비교해서 로그 기록
  5. 갱신된 복사본을 원본 경로로 교체
  6. git add/commit/push

실패해도 원본 파일은 절대 건드리지 않고(1단계에서 복사본에만 작업), 로그 파일에
결과를 남긴다 - 사람이 옆에 없어도 나중에 로그로 성공/실패를 확인할 수 있음.
#>

$ErrorActionPreference = "Stop"

$RepoDir = "C:\Users\infomax\Desktop\이향기\TradingCheckboard"
$RawDataPath = Join-Path $RepoDir "data\RawData.xlsx"
$TempCopyPath = Join-Path $env:TEMP "RawData_autoupdate_working.xlsx"
$LogDir = Join-Path $RepoDir "scripts\logs"
$LogPath = Join-Path $LogDir "update_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
$XllPath = "C:\Infomax\bin\excel64\imxlexcelai64.xll"

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -Path $LogPath -Value $line -Encoding utf8
    Write-Output $line
}

# 주말엔 Infomax 데이터 자체가 갱신 안 되니 그냥 스킵 (에러 아님)
$today = Get-Date
if ($today.DayOfWeek -eq [DayOfWeek]::Saturday -or $today.DayOfWeek -eq [DayOfWeek]::Sunday) {
    Write-Log "주말이라 스킵합니다."
    exit 0
}

Write-Log "=== RawData.xlsx 자동 업데이트 시작 ==="

if (-not (Test-Path $RawDataPath)) {
    Write-Log "ERROR: 원본 파일을 찾을 수 없습니다: $RawDataPath"
    exit 1
}

# 이전 실행이 비정상 종료돼서 임시파일이 남아있거나 Excel 프로세스가 그걸 붙잡고 있을 수 있음 -
# 매번 깨끗한 상태에서 시작하도록 자가 복구
if (Test-Path $TempCopyPath) {
    Get-Process -Name EXCEL -ErrorAction SilentlyContinue | Where-Object { -not $_.MainWindowTitle } |
        ForEach-Object {
            Write-Log "이전 실행의 남은 Excel 프로세스(PID $($_.Id)) 정리"
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 2
    Remove-Item $TempCopyPath -Force -ErrorAction SilentlyContinue
}

Copy-Item -Path $RawDataPath -Destination $TempCopyPath -Force
Write-Log "임시 복사본 생성: $TempCopyPath"

$excel = $null
$success = $false
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $wb = $excel.Workbooks.Open($TempCopyPath)
    Start-Sleep -Seconds 5

    if ($wb.ReadOnly) {
        Write-Log "ERROR: 파일이 읽기 전용으로 열렸습니다 (원본이 다른 곳에서 열려있을 수 있음)."
        throw "read-only open"
    }

    $reg = $excel.RegisterXLL($XllPath)
    Write-Log "RegisterXLL => $reg"

    # 갱신 전 각 시트 최신 날짜 기록
    $beforeDates = @{}
    foreach ($ws in $wb.Sheets) {
        try {
            $lastRow = $ws.Cells(1, 1).End(4).Row
            $beforeDates[$ws.Name] = $ws.Cells($lastRow, 1).Value2
        } catch {}
    }

    $excel.Run("IMxl_OnRefreshData")
    Write-Log "재조회 매크로 실행 완료, 재계산 대기 중..."
    # 매크로 직후 Excel이 내부적으로 바쁜 상태라 바로 다른 COM 호출을 하면
    # RPC_E_CALL_REJECTED(재진입 거부) 에러가 남 - 충분히 기다린 뒤 재시도 로직으로 호출
    Start-Sleep -Seconds 30

    $calcTriggered = $false
    for ($try = 1; $try -le 5; $try++) {
        try {
            $excel.CalculateFull()
            $calcTriggered = $true
            break
        } catch {
            Write-Log "CalculateFull 재시도 $try/5 (Excel busy: $($_.Exception.Message))"
            Start-Sleep -Seconds 15
        }
    }
    if (-not $calcTriggered) {
        Write-Log "CalculateFull 실패했지만 계속 진행 (재조회 자체는 백그라운드에서 진행 중일 수 있음)"
    }

    # 최대 5분간 30초 간격으로 계산 완료 대기 (CalculationState 조회도 바쁘면 실패할 수 있어 방어)
    $maxWaitSeconds = 300
    $waited = 0
    while ($waited -lt $maxWaitSeconds) {
        Start-Sleep -Seconds 30
        $waited += 30
        try {
            if ($excel.CalculationState -eq 1) { break }  # 1 = xlDone
        } catch {
            # 조회 중 바쁘면 다음 루프에서 재시도
        }
    }
    Write-Log "재계산 대기 종료 (경과 ${waited}초)"

    $saved = $false
    for ($try = 1; $try -le 3; $try++) {
        try {
            $wb.Save()
            $saved = $true
            break
        } catch {
            Write-Log "저장 재시도 $try/3 (Excel busy: $($_.Exception.Message))"
            Start-Sleep -Seconds 15
        }
    }
    if (-not $saved) { throw "저장 실패 (Excel이 계속 바쁜 상태)" }
    Write-Log "저장 완료"

    foreach ($ws in $wb.Sheets) {
        try {
            $lastRow = $ws.Cells(1, 1).End(4).Row
            $after = $ws.Cells($lastRow, 1).Value2
            $before = $beforeDates[$ws.Name]
            $changed = "동일"
            if ($after -ne $before) { $changed = "변경됨" }
            Write-Log "  [$($ws.Name)] before=$before after=$after ($changed)"
        } catch {}
    }

    $success = $true
} catch {
    Write-Log "ERROR: $($_.Exception.Message)"
} finally {
    if ($excel) {
        try { $excel.Workbooks | ForEach-Object { $_.Close($false) } } catch {}
        try { $excel.Quit() } catch {}
        [System.Runtime.Interopservices.Marshal]::ReleaseComObject($excel) | Out-Null
    }
}

if (-not $success) {
    Write-Log "재조회 실패 - 원본 파일은 건드리지 않고 종료합니다."
    Remove-Item $TempCopyPath -Force -ErrorAction SilentlyContinue
    exit 1
}

Copy-Item -Path $TempCopyPath -Destination $RawDataPath -Force
Remove-Item $TempCopyPath -Force -ErrorAction SilentlyContinue
Write-Log "원본 파일 교체 완료"

Set-Location $RepoDir
# git push 등은 정상 진행 상황도 stderr로 출력하는 경우가 많은데, $ErrorActionPreference=Stop 상태에서
# 2>&1로 합치면 그게 다 터미네이팅 에러로 취급돼서 스크립트가 죽는다 - git 구간만 Continue로 완화
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"

& git add "data/RawData.xlsx" 2>&1 | ForEach-Object { Write-Log "git: $_" }
$diffCheck = & git diff --cached --stat 2>&1
if ($diffCheck) {
    & git commit -m "RawData.xlsx 자동 업데이트 ($(Get-Date -Format 'yyyy-MM-dd'))" 2>&1 | ForEach-Object { Write-Log "git: $_" }
    & git push origin main 2>&1 | ForEach-Object { Write-Log "git: $_" }
    if ($LASTEXITCODE -eq 0) {
        Write-Log "git 커밋/푸시 완료"
    } else {
        Write-Log "WARNING: git push exit code = $LASTEXITCODE (로그 확인 필요)"
    }
} else {
    Write-Log "변경사항 없음 - git 커밋 스킵"
}
$ErrorActionPreference = $prevEAP

Write-Log "=== 완료 ==="
