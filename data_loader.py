"""
인포맥스 RawData.xlsx 파서.

data/RawData.xlsx 는 인포맥스 IMDH 함수로 뽑은 일별 히스토리 원본이다.
시트 구조 (고정 레이아웃, 매일 인포맥스가 값만 새로 채워넣는 방식):

  Info(일):
    row2 = 블록 제목 (블록의 첫 컬럼에만 값, IMDH 수식)
    row3 = 블록 내 서브헤더 (만기 라벨 등)
    row4~ = A열 날짜, 나머지 컬럼은 값 (최근 날짜가 위, 아래로 갈수록 과거)
    블록들: 국고채권/통안증권/각종 신용채권 커브(만기 8개: 1,2,3,4,5,10,20,30년),
            원화 IRS 종합코드 6개월~30년 (만기별 단일 컬럼)

  Info(단기금리):
    같은 레이아웃, 기준금리/CD(91일) 등 단일 계열들

이 모듈은 컬럼 위치를 하드코딩하지 않고 row2/row3 를 스캔해서 블록을 자동으로 찾는다 -
인포맥스에서 커브를 추가/삭제해도 구조만 유지되면 그대로 동작한다.

성능: row1 F열("Data 개수")을 크게 잡으면(예: 99999) A열 날짜가 시작일부터 쭉 몇만 행 채워지고
실데이터는 그 중간 어딘가에만 있어서 "위에서 N행만" 식으로는 못 자른다 - 그냥 전체를 순차로
훑되(read_only + iter_rows(values_only=True), cell() 랜덤 접근 없음), 실제 값이 있는 행만
결과에 담는다. 9MB 파일 기준 5~6초 정도라 매 위젯 조작마다 다시 돌면 안 되므로
load_raw_data() 는 파일 mtime 을 키로 st.cache_data 캐싱한다 - 파일이 안 바뀌면 재파싱 안 함.
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"
EXCEL_PATH = DATA_DIR / "RawData.xlsx"

SHEET_DAILY = "Info(국내금리)"
SHEET_SHORT = "Info(단기금리)"

TENOR_ORDER = [
    "91D", "6M", "9M", "1Y", "1.5Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y",
    "9Y", "10Y", "11Y", "12Y", "15Y", "20Y", "25Y", "30Y",
]

# 국고채/통안채/신용채권 커브 블록 제목에서 상품명만 뽑기 위한 접두/치환 규칙
_CURVE_PREFIX = "시가평가 4사평균 "
_IRS_PREFIX = "원화 IRS 종합코드 "
_GROUP_RENAME = {"국고채권": "국고채", "통안증권": "통안채"}

CREDIT_GROUPS_ORDER = [
    "국고채", "통안채", "은행채AAA",
    "카드채AA+", "카드채AA", "카드채AA-", "카드채A+",
    "기타금융채AA+", "기타금융채AA0", "기타금융채AA-", "기타금융채A+", "기타금융채A0",
    "회사채AAA", "회사채AA+", "회사채AA0", "회사채AA-", "회사채A+", "회사채A0", "회사채A-",
]


def _normalize_tenor(label: str) -> str | None:
    label = (label or "").strip()
    m = re.match(r"^(\d+)년이하", label)
    if m:
        return f"{m.group(1)}Y"
    if label == "18개월":
        return "1.5Y"
    m = re.match(r"^(\d+)개월$", label)
    if m:
        months = int(m.group(1))
        return f"{months // 12}Y" if months % 12 == 0 else f"{months}M"
    m = re.match(r"^(\d+)년$", label)
    if m:
        return f"{m.group(1)}Y"
    return None


def _find_blocks(row2: tuple) -> list[tuple[int, int, str]]:
    """
    row2(0-based 튜플) 를 스캔해서 (시작컬럼, 끝컬럼, 블록제목) 리스트 반환.
    블록 제목 셀은 항상 IMDH 수식의 계산 결과(상품명)이고, "단위: %" 같은 순수
    텍스트 주석은 "단위"로 시작하므로 블록 시작으로 오인하지 않는다.
    """
    starts = [
        c for c, v in enumerate(row2)
        if isinstance(v, str) and v.strip() and not v.strip().startswith("단위")
    ]
    starts.append(len(row2))
    return [(starts[i], starts[i + 1] - 1, row2[starts[i]]) for i in range(len(starts) - 1)]


def _block_to_group_tenor(title: str, sub) -> tuple[str, str] | None:
    title = (title or "").strip()
    if title.startswith(_CURVE_PREFIX):
        name = title[len(_CURVE_PREFIX):].replace("(공모/무보증)", "")
        name = name.replace("금융채 ", "").replace(" ", "")
        name = _GROUP_RENAME.get(name, name)
        tenor = _normalize_tenor(sub)
        return (name, tenor) if tenor else None
    if title.startswith(_IRS_PREFIX):
        tenor = _normalize_tenor(title[len(_IRS_PREFIX):])
        return ("IRS", tenor) if tenor else None
    if "CD(91일물)" in title:
        return ("CD", "91D")
    if title == "한국:기준금리":
        return ("기준금리", None)
    return None


_HARD_ROW_CAP = 200_000  # 절대적인 안전장치 - 정상 시나리오에서는 도달하지 않음


def _parse_sheet(ws) -> pd.DataFrame:
    rows_iter = ws.iter_rows(values_only=True)
    try:
        next(rows_iter)  # row1 (설정값 - "Data 개수" 등, 신뢰하지 않고 실제 값 유무로 판단)
        row2 = next(rows_iter)
        row3 = next(rows_iter)
    except StopIteration:
        return pd.DataFrame(columns=["날짜", "그룹", "만기", "값"])

    col_map: dict[int, tuple[str, str]] = {}
    for start, end, title in _find_blocks(row2):
        for c in range(start, end + 1):
            if c == 0:
                continue  # A열은 날짜 칼럼이라 데이터로 취급하지 않음
            sub = row3[c] if c < len(row3) else None
            mapped = _block_to_group_tenor(title, sub)
            if mapped is not None:
                col_map[c] = mapped

    rows = []
    for i, r in enumerate(rows_iter):
        if i >= _HARD_ROW_CAP:
            break
        date = r[0] if len(r) > 0 else None
        if date is None:
            continue  # 실데이터가 맨 위부터 연속이라는 보장이 없어서 건너뛰기만 함
        for c, (group, tenor) in col_map.items():
            val = r[c] if c < len(r) else None
            if val in (None, "", 0):
                continue
            rows.append({"날짜": date, "그룹": group, "만기": tenor, "값": val})
    return pd.DataFrame(rows)


@st.cache_data(show_spinner="RawData.xlsx 불러오는 중...")
def _load_raw_data_cached(_mtime: float) -> tuple[pd.DataFrame, bool]:
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True, read_only=True)
    try:
        df_daily = _parse_sheet(wb[SHEET_DAILY]) if SHEET_DAILY in wb.sheetnames else pd.DataFrame()
        df_short = _parse_sheet(wb[SHEET_SHORT]) if SHEET_SHORT in wb.sheetnames else pd.DataFrame()
    finally:
        wb.close()

    df = pd.concat([df_daily, df_short], ignore_index=True).drop_duplicates(subset=["날짜", "그룹", "만기"])
    df["날짜"] = pd.to_datetime(df["날짜"])
    tenor_cat = [t for t in TENOR_ORDER if t in df["만기"].unique()] + \
                [t for t in df["만기"].dropna().unique() if t not in TENOR_ORDER]
    df["만기"] = pd.Categorical(df["만기"], categories=tenor_cat, ordered=True)
    return df.sort_values(["그룹", "날짜", "만기"]).reset_index(drop=True), False


def load_raw_data() -> tuple[pd.DataFrame, bool]:
    """
    (long-format DataFrame [날짜, 그룹, 만기, 값], is_sample) 반환.
    RawData.xlsx 가 없으면 빈 데이터프레임 + is_sample=True.
    파일이 바뀌지 않는 한(mtime 기준) 캐시된 결과를 재사용한다.
    """
    if not EXCEL_PATH.exists():
        return pd.DataFrame(columns=["날짜", "그룹", "만기", "값"]), True
    return _load_raw_data_cached(EXCEL_PATH.stat().st_mtime)


def latest_curve(df: pd.DataFrame, group: str) -> pd.DataFrame:
    sub = df[df["그룹"] == group]
    if sub.empty:
        return sub
    latest_date = sub["날짜"].max()
    return sub[sub["날짜"] == latest_date].sort_values("만기")


def curve_history(df: pd.DataFrame, group: str, tenor: str) -> pd.DataFrame:
    sub = df[(df["그룹"] == group) & (df["만기"] == tenor)]
    return sub.sort_values("날짜")


def credit_spread(df: pd.DataFrame, group: str, base_group: str = "국고채") -> pd.DataFrame:
    """group 커브 - base_group 커브, 만기 매칭해서 bp 스프레드."""
    a = df[df["그룹"] == group][["날짜", "만기", "값"]].rename(columns={"값": "그룹금리"})
    b = df[df["그룹"] == base_group][["날짜", "만기", "값"]].rename(columns={"값": "기준금리"})
    merged = a.merge(b, on=["날짜", "만기"], how="inner")
    merged["스프레드_bp"] = (merged["그룹금리"] - merged["기준금리"]) * 100
    return merged.sort_values(["날짜", "만기"])
