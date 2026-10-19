"""매일 데이터 업데이트 루틴 (Windows 작업 스케줄러가 평일 아침에 실행).

  1. RawData_master.xlsx 업데이트: 인포맥스 IMDH 재조회(refresh_master.ps1) 후
     "전 영업일 날짜"가 시트별로 채워졌는지 확인 -> 통과해야 마스터를 교체
  2. market.db 업데이트: 마스터의 값을 DB에 반영 (엑셀 값이 있는 칸만 덮어쓰고 없는 날짜는 추가)
  3. 배포: market_recent.db(기준일 이후분, 수 MB 이하)를 다시 만들고 GitHub Pages 정적 사이트(docs/index.html)도
     DB 기준으로 다시 생성해서 GitHub 에 푸시 -> Streamlit Cloud 재배포 + Pages 갱신
     (과거분 market_history.db 는 거의 안 바뀌어서 매일 올리지 않음. 배포 실패는 경고만 하고 업데이트 자체는 성공 처리)

실패(인포맥스 미로그인, 재조회 실패, 전 영업일 데이터 미반영)하면 --retry-minutes 간격으로
--max-attempts 번까지 다시 시도하고, 끝까지 안 되면 exit 1 (마스터/DB는 건드리지 않음).

사용: python daily_update.py [--force] [--max-attempts 4] [--retry-minutes 30] [--skip-excel]
  --force       주말에도 실행
  --skip-excel  엑셀 재조회 없이 현재 마스터로 확인 + DB 업데이트만 (수동 갱신 후 사용)
  --no-push     GitHub 푸시 생략 (로컬 DB 업데이트만)
  --no-static   정적 사이트(docs/index.html) 재생성 생략
"""

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
sys.path.insert(0, _HERE)

import openpyxl
import pandas as pd

import build_sqlite_db as B
import export_deploy_db as X
import update_sqlite_db as U

REPO = os.path.dirname(_HERE)
DATA_DIR = os.path.join(REPO, "data")
MASTER = os.path.join(DATA_DIR, "RawData_master.xlsx")
DB = os.path.join(DATA_DIR, "market.db")
LOG_DIR = os.path.join(_HERE, "logs")
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
REFRESH_PS1 = os.path.join(_HERE, "refresh_master.ps1")

MIN_OVERALL = 0.85   # 전 영업일 값이 있는 시리즈 비율(전체)
MIN_SHEET = 0.50     # 시트별 최소 비율
GAP_COVERAGE = 0.60  # DB에서 "그 날짜는 채워졌다"고 보는 시트 내 시리즈 비율

LOG_PATH = os.path.join(LOG_DIR, f"daily_{dt.date.today():%Y%m%d}.log")


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def prev_weekday(d):
    d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def infomax_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq infomaxmain.exe"], capture_output=True).stdout
    return b"infomaxmain.exe" in out.lower()


def coverage_start(today):
    """DB 최근 60일에서 시트별로 평일인데 시리즈 대부분이 비어 있는 날짜(백필 대상)를 찾아 그 이틀 전부터 재조회한다.
    빈 구간이 없으면 최근 10일을 다시 받는다(최신 값 정정 반영).
    (주말/휴일은 인포맥스가 직전 값으로 채워주므로 평일만 본다)"""
    con = sqlite3.connect(DB)
    try:
        ser = pd.read_sql_query("SELECT column_name, sheet FROM series", con)
        lo = (today - dt.timedelta(days=60)).isoformat()
        wide = pd.read_sql_query("SELECT * FROM market WHERE date >= ? AND date < ?", con, params=(lo, today.isoformat())).set_index("date")
    finally:
        con.close()
    weekdays = [d for d in wide.index if dt.date.fromisoformat(d).weekday() < 5]
    first_bad = None
    for _sheet, g in ser.groupby("sheet"):
        cov = wide.loc[weekdays, [c for c in g["column_name"] if c in wide.columns]].notna().mean(axis=1)
        bad = cov[cov < GAP_COVERAGE]
        if len(bad):
            d = dt.date.fromisoformat(bad.index.min())
            first_bad = d if first_bad is None else min(first_bad, d)
    default = today - dt.timedelta(days=10)
    if first_bad is None:
        return default
    return min(first_bad - dt.timedelta(days=2), default)


def run_refresh(start_date, out_path):
    cmd = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", REFRESH_PS1,
           "-Master", MASTER, "-Out", out_path, "-StartDate", start_date.isoformat()]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in p.stdout:
        line = raw.decode("cp949", errors="replace").rstrip()
        if line:
            log("  " + line)
    return p.wait()


def check_freshness(path, expected):
    """각 시트에서 최근 활성 시리즈 중 expected 날짜 값이 있는 비율을 계산한다."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    exp = expected.isoformat()
    rows, tot_ok, tot_n, stale = [], 0, 0, []
    for ws in wb.worksheets:
        df, hdr = B.read_sheet(ws, dt.date(2099, 12, 31))
        n = df.shape[1]
        if n == 0:
            rows.append((ws.title, 0, 0, 0.0))
            continue
        ok_mask = df.loc[exp].notna() if exp in df.index else pd.Series(False, index=df.columns)
        ok = int(ok_mask.sum())
        rows.append((ws.title, ok, n, ok / n))
        tot_ok += ok
        tot_n += n
        stale += [f"{ws.title}:{hdr[c][2]}" for c in df.columns[~ok_mask.values]]
    wb.close()
    overall = tot_ok / tot_n if tot_n else 0.0
    passed = overall >= MIN_OVERALL and all(r[3] >= MIN_SHEET for r in rows)
    return passed, overall, rows, stale


def swap_in(src, dst, backup=None):
    if backup and os.path.exists(dst):
        shutil.copyfile(dst, backup)
    for i in range(5):
        try:
            os.replace(src, dst)
            return True
        except PermissionError:
            time.sleep(3)
    return False


def update_db(master_path, expected):
    tmp = DB + ".tmp"
    shutil.copyfile(DB, tmp)
    con = sqlite3.connect(tmp)
    before = con.execute("SELECT COUNT(*) FROM market").fetchone()[0]
    con.close()
    wb = openpyxl.load_workbook(master_path, data_only=True, read_only=True)
    sheets = list(wb.sheetnames)
    wb.close()
    for sh in sheets:
        r = U.update_sheet(tmp, master_path, sh, drop_missing=False)
        note = []
        if r["added"]:
            note.append(f"신규 칼럼 {len(r['added'])}")
        if r["missing"]:
            note.append(f"엑셀에 없는 칼럼 {len(r['missing'])}(DB 유지)")
        log(f"  [DB] {sh}: 시리즈 {r['cols']}, 값 변경 {r['changed_existing_cells']}" + (" | " + ", ".join(note) if note else ""))
    con = sqlite3.connect(tmp)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    after, last = con.execute("SELECT COUNT(*), MAX(date) FROM market").fetchone()
    con.close()
    if not ok or after < before or last < expected.isoformat():
        os.remove(tmp)
        raise RuntimeError(f"DB 검증 실패 (integrity={ok}, 행 {before}->{after}, 최신일 {last})")
    if not swap_in(tmp, DB, DB + ".bak"):
        raise RuntimeError("market.db 교체 실패 (다른 프로그램이 DB를 열고 있음)")
    log(f"  [DB] 완료: {after}일, 최신일 {last}")


def _git(*cmd):
    p = subprocess.run(["git", "-C", REPO, *cmd], capture_output=True)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", errors="replace").strip()


def build_static_site():
    script = os.path.join(_HERE, "generate_static_site.py")
    p = subprocess.run([sys.executable, "-X", "utf8", "-W", "ignore", script], capture_output=True, cwd=REPO)
    out = (p.stdout + p.stderr).decode("utf-8", errors="replace").strip().splitlines()
    done = [l for l in out if l.startswith("완료")]
    if p.returncode != 0 or not done:
        raise RuntimeError("정적 사이트 생성 실패: " + " | ".join(out[-3:])[:300])
    log("  [배포] 정적 사이트 생성 " + done[-1].split("\\")[-1])


def publish(today, with_static=True):
    info = X.export()
    log(f"  [배포] market_recent.db: {info['recent_rows']}일(기준일 {info['history_cutoff']} 이후), 최신 {info['recent_last']}, {info['recent_mb']}MB")
    files = ["data/market_recent.db"]
    if with_static:
        try:
            build_static_site()
            files.append("docs/index.html")
        except Exception as e:  # noqa: BLE001 - 정적 사이트 실패해도 recent DB 배포는 계속
            log(f"WARNING: {e}")
    _git("add", *files)
    rc, out = _git("status", "--porcelain", "--", *files)
    if not out.strip():
        log("  [배포] 변경사항 없음 - 커밋/푸시 생략")
        return
    rc, out = _git("commit", "-m", f"데이터 업데이트 ({today})", "--", *files)
    log(f"  [git] commit rc={rc}")
    if rc != 0:
        raise RuntimeError("git commit 실패: " + out[:200])
    rc, out = _git("push", "origin", "main")
    if rc != 0:
        raise RuntimeError("git push 실패: " + out[:300])
    log("  [배포] GitHub 푸시 완료")


def attempt(args, today, expected):
    work = os.path.join(os.environ.get("TEMP", DATA_DIR), "RawData_master_daily.xlsx")
    if args.skip_excel:
        work = MASTER
        log("엑셀 재조회 생략(--skip-excel): 현재 마스터 파일 사용")
    else:
        if not infomax_running():
            log("인포맥스(infomaxmain)가 실행 중이 아님 - 로그인 필요")
            return False
        start = coverage_start(today)
        log(f"재조회 시작일: {start} (DB에서 비어 있는 구간 포함)")
        rc = run_refresh(start, work)
        if rc != 0:
            log(f"엑셀 재조회 실패 (exit {rc})")
            return False
    passed, overall, rows, stale = check_freshness(work, expected)
    log(f"전 영업일({expected}) 반영 확인: 전체 {overall:.1%}")
    for name, ok, n, frac in rows:
        log(f"   {name}: {ok}/{n} ({frac:.0%})")
    if not passed:
        log(f"전 영업일 데이터가 충분히 채워지지 않음 (기준 전체 {MIN_OVERALL:.0%}, 시트 {MIN_SHEET:.0%}) - 미반영 예: {stale[:6]}")
        return False
    master_for_db = MASTER
    if not args.skip_excel:
        if swap_in(work, MASTER, os.path.join(DATA_DIR, "RawData_master.prev.xlsx")):
            log("RawData_master.xlsx 교체 완료")
        else:
            master_for_db = os.path.join(DATA_DIR, "RawData_master.new.xlsx")
            shutil.copyfile(work, master_for_db)
            log(f"WARNING: 마스터가 다른 프로그램(Excel)에서 열려 있어 교체 못함 -> {master_for_db} 로 저장")
    update_db(master_for_db, expected)
    if not args.no_push:
        try:
            publish(today, with_static=not args.no_static)
        except Exception as e:  # noqa: BLE001 - 배포 실패가 데이터 업데이트 성공을 뒤집지 않게
            log(f"WARNING: 배포 단계 실패 (로컬 DB는 정상 업데이트됨): {type(e).__name__}: {e}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-excel", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-static", action="store_true")
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--retry-minutes", type=int, default=30)
    args = ap.parse_args()

    today = dt.date.today()
    if today.weekday() >= 5 and not args.force:
        log("주말이라 건너뜁니다")
        return 0
    expected = prev_weekday(today)
    log(f"=== 일일 업데이트 시작 (오늘 {today}, 전 영업일 {expected}) ===")
    for n in range(1, args.max_attempts + 1):
        log(f"--- 시도 {n}/{args.max_attempts} ---")
        try:
            if attempt(args, today, expected):
                log("=== 완료 ===")
                return 0
        except Exception as e:  # noqa: BLE001
            log(f"ERROR: {type(e).__name__}: {e}")
        if n < args.max_attempts:
            log(f"{args.retry_minutes}분 후 재시도")
            time.sleep(args.retry_minutes * 60)
    log("=== 실패: 마스터/DB 미변경 ===")
    return 1


if __name__ == "__main__":
    sys.exit(main())
