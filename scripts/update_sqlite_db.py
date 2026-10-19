"""market.db 증분 업데이트: 마스터 엑셀의 특정 시트 값을 DB에 반영한다.

- 칼럼 매칭은 위치가 아니라 이름(시트__블록제목-필드)으로 한다 (엑셀에서 열을 지워도 안 밀림).
- 엑셀 값이 있는 칸만 덮어쓴다(엑셀이 비어 있으면 기존 DB 값 유지). 없는 날짜는 행을 추가한다.
- 엑셀에 없고 DB에만 있는 칼럼은 --drop-missing 을 줄 때만 삭제한다.

사용: python update_sqlite_db.py --update RawData_master.xlsx --sheet "Info(원자재)" [--drop-missing]
"""

import argparse
import datetime as dt
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_sqlite_db as B
import openpyxl

DATA_DIR = B.DATA_DIR


def update_sheet(db_path, xlsx_path, sheet, drop_missing=False):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    df, hdr = B.read_sheet(wb[sheet], dt.date(2099, 12, 31))
    wb.close()
    short = B._short(sheet)
    names, used = {}, set()
    for i in df.columns:
        title, field, name = hdr[i]
        col, k = f"{short}__{name}", 2
        base = col
        while col in used:
            col, k = f"{base}#{k}", k + 1
        used.add(col)
        names[i] = (col, title, field)
    df.columns = [names[i][0] for i in df.columns]

    con = sqlite3.connect(db_path)
    db_cols = {r[0] for r in con.execute("SELECT column_name FROM series WHERE sheet=?", (sheet,))}
    missing = sorted(db_cols - set(df.columns))
    added = [c for c in df.columns if c not in db_cols]

    for c in added:
        i = next(i for i, v in names.items() if v[0] == c)
        con.execute(f'ALTER TABLE market ADD COLUMN "{c}" REAL')
        con.execute("INSERT INTO series VALUES (?,?,?,?,?)", (c, sheet, i + 1, names[i][1], names[i][2]))

    con.executemany("INSERT OR IGNORE INTO market(date) VALUES (?)", [(d,) for d in df.index])
    n_changed = 0
    for c in df.columns:
        s = df[c].dropna()
        old = dict(con.execute(f'SELECT date, "{c}" FROM market WHERE "{c}" IS NOT NULL').fetchall()) if c not in added else {}
        n_changed += sum(1 for d, v in s.items() if d in old and abs(old[d] - v) > 1e-9)
        con.executemany(f'UPDATE market SET "{c}"=? WHERE date=?', [(float(v), d) for d, v in s.items()])

    dropped = []
    if drop_missing:
        for c in missing:
            con.execute(f'ALTER TABLE market DROP COLUMN "{c}"')
            con.execute("DELETE FROM series WHERE column_name=?", (c,))
            dropped.append(c)

    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"updated_{sheet}", dt.datetime.now().isoformat(timespec="seconds")))
    con.commit()
    con.close()
    return {"rows": len(df), "cols": df.shape[1], "added": added, "missing": missing, "dropped": dropped, "changed_existing_cells": n_changed}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(DATA_DIR, "market.db"))
    ap.add_argument("--update", default=os.path.join(DATA_DIR, "RawData_master.xlsx"))
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--drop-missing", action="store_true")
    a = ap.parse_args()
    print(update_sheet(a.db, a.update, a.sheet, a.drop_missing))
