"""배포용 DB 파일 생성: market.db(로컬 전체) -> market_history.db + market_recent.db.

  market_history.db : 기준일(history_cutoff) 이전 데이터 - 한 번 만들어 올리면 거의 안 바뀜(수십 MB)
  market_recent.db  : 기준일 이후 데이터 - 매일 다시 만들어 올림(수 MB 이하, 하루 1행씩 늘어남)

앱(data_loader)은 두 파일을 합쳐 읽고 같은 날짜/시리즈는 recent 가 우선한다.
기준일은 history 파일의 meta('history_cutoff')에 저장돼 있어서 recent 는 항상 그 이후 전체를 담는다.

사용: python export_deploy_db.py                      # recent 만 갱신 (history 가 없으면 --history-cutoff 필요)
      python export_deploy_db.py --history-cutoff 2026-09-30 --rebuild-history   # history 도 다시 생성
"""

import argparse
import os
import shutil
import sqlite3

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SRC = os.path.join(DATA_DIR, "market.db")
HISTORY = os.path.join(DATA_DIR, "market_history.db")
RECENT = os.path.join(DATA_DIR, "market_recent.db")


def build_history(cutoff):
    shutil.copyfile(SRC, HISTORY)
    con = sqlite3.connect(HISTORY)
    con.execute("DELETE FROM market WHERE date > ?", (cutoff,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('history_cutoff', ?)", (cutoff,))
    con.commit()
    con.execute("VACUUM")
    con.close()


def history_cutoff():
    con = sqlite3.connect(HISTORY)
    try:
        row = con.execute("SELECT value FROM meta WHERE key='history_cutoff'").fetchone()
    finally:
        con.close()
    if not row:
        raise RuntimeError("market_history.db 에 history_cutoff 메타가 없음 (--rebuild-history 필요)")
    return row[0]


def build_recent(cutoff):
    tmp = RECENT + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect(SRC)
    cols = [r[1] for r in src.execute("PRAGMA table_info(market)")]
    series = src.execute("SELECT * FROM series").fetchall()
    series_cols = [r[1] for r in src.execute("PRAGMA table_info(series)")]
    src.close()

    con = sqlite3.connect(tmp)
    col_sql = ", ".join("date TEXT PRIMARY KEY" if c == "date" else f'"{c}" REAL' for c in cols)
    con.execute(f"CREATE TABLE market ({col_sql})")
    con.execute(f"CREATE TABLE series ({', '.join(series_cols)})")
    con.executemany(f"INSERT INTO series VALUES ({','.join('?' * len(series_cols))})", series)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("ATTACH DATABASE ? AS src", (SRC,))
    con.execute("INSERT INTO market SELECT * FROM src.market WHERE date > ?", (cutoff,))
    n, last = con.execute("SELECT COUNT(*), MAX(date) FROM market").fetchone()
    con.execute("INSERT INTO meta VALUES ('history_cutoff', ?)", (cutoff,))
    con.execute("INSERT INTO meta VALUES ('rows', ?)", (str(n),))
    con.commit()
    con.execute("DETACH DATABASE src")
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, RECENT)
    return n, last


def export(cutoff=None, rebuild_history=False):
    if rebuild_history or not os.path.exists(HISTORY):
        if not cutoff:
            raise RuntimeError("history 를 만들려면 --history-cutoff 가 필요합니다")
        build_history(cutoff)
    cut = history_cutoff()
    n, last = build_recent(cut)
    return {"history_cutoff": cut, "recent_rows": n, "recent_last": last,
            "history_mb": round(os.path.getsize(HISTORY) / 1e6, 1), "recent_mb": round(os.path.getsize(RECENT) / 1e6, 2)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history-cutoff", default=None)
    ap.add_argument("--rebuild-history", action="store_true")
    a = ap.parse_args()
    print(export(a.history_cutoff, a.rebuild_history))
