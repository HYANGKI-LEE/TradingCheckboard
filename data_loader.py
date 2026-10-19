"""
인포맥스 일별 시세 SQLite DB(data/market.db) 로더.

market.db 는 인포맥스 IMDH 엑셀(RawData_master.xlsx)을 매일 scripts/daily_update.py 가 읽어서
채운 통합 와이드 테이블이다.

  market(date, "<시트>__<시리즈명>" ...)   - 날짜 1행, 시리즈 1칼럼
  series(column_name, sheet, col_idx, block_title, field)

이 모듈은 series 의 (시트, 블록제목, 필드)를 대시보드가 쓰는 (그룹, 만기) 로 매핑한 뒤
long-format DataFrame [날짜, 그룹, 만기, 값] 으로 돌려준다. 엑셀 시절의 매핑 규칙을 그대로 쓰므로
인포맥스에서 시리즈를 추가/삭제해도 블록 제목/필드 규칙만 지키면 그대로 동작한다.

배포(Streamlit Cloud)에서는 market.db(전체, 깃 미추적) 대신 market_history.db(기준일 이전, 거의 안 바뀜) +
market_recent.db(기준일 이후, 매일 갱신) 두 파일을 합쳐 읽는다 - 같은 날짜/시리즈는 recent 가 우선.
data_version() 이 사용 중인 DB 파일들의 최신 mtime 을 돌려주므로 캐시 키로 쓰면 된다.

load_raw_data() 는 data_version() 을 키로 st.cache_data 캐싱한다 - DB가 안 바뀌면 재조회 안 함.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "market.db"
HISTORY_DB_PATH = DATA_DIR / "market_history.db"
RECENT_DB_PATH = DATA_DIR / "market_recent.db"

SHEET_DAILY = "Info(국내금리)"
SHEET_SHORT = "Info(단기금리)"
SHEET_FOREIGN = "Info(해외금리)"
SHEET_FX = "Info(FX)"
SHEET_COMMODITY = "Info(원자재)"
SHEET_IRS_DETAIL = "Info(IRS)"
SHEET_FUTURES = "Info(선물)"
SHEET_STOCK = "Info(주가)"
SHEET_CREDIT_DETAIL = "Info(크레딧)"

# 국채선물 연결(3/5/10/30년): 그룹은 "선물{N}년", "만기" 자리에는 필드명(현재가/이론가/저평가/수정듀레이션/내재수익률)이 들어간다
FUTURES_TENORS = ["3", "5", "10", "30"]
FUTURES_FIELDS = ["현재가", "이론가", "저평가", "수정듀레이션", "내재수익률"]
_FUTURES_RE = re.compile(r"^(\d+)년국채 연결$")

# IRS Zero/Forward curve 만기 순서 (01M~11M, 01Y, 18M(=1.5Y), 02Y~50Y - 시트의 서브헤더 순서 그대로)
IRS_ZERO_FWD_TENOR_ORDER = [
    "1M", "2M", "3M", "4M", "5M", "6M", "7M", "8M", "9M", "10M", "11M",
    "1Y", "1.5Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y", "9Y", "10Y",
    "11Y", "12Y", "13Y", "14Y", "15Y", "16Y", "17Y", "18Y", "19Y", "20Y",
    "25Y", "30Y", "35Y", "40Y", "45Y", "50Y",
]

# 원자재는 "WTI 2026-10 (연결선물)" 처럼 제목에 연결선물 만기 월이 붙어있고, 롤오버되면
# 다음달엔 "2026-11" 로 바뀐다 - 그 월/연도 꼬리표를 떼어내서 매달 같은 그룹으로 이어지게 한다.
COMMODITY_ORDER = [
    "WTI", "브렌트", "두바이유", "천연가스", "팔라듐", "백금", "블룸버그 상품 지수", "에탄올",
    "KC HRW 밀", "다우 존스 부동산", "미니 옥수수", "미니 콩", "미니 소맥", "옥수수", "대두유",
    "대두박", "귀리", "30 DAY FEDERAL FUNDS", "쌀", "대두", "시카고 SRW 밀", "버터", "치즈",
    "3등급 우유", "4등급 우유", "비육우", "무지방 건조우유", "돈육", "생우", "코코아", "면화",
    "미국달러지수", "커피", "오렌지주스", "설탕", "금", "은", "구리", "알루미늄",
]

FOREIGN_COUNTRIES_ORDER = [
    "미국", "독일", "영국", "프랑스", "이탈리아", "일본", "호주", "캐나다",
    "인도", "인도네시아", "브라질", "멕시코",
]

TENOR_ORDER = [
    "91D", "6M", "9M", "1Y", "1.5Y", "2Y", "3Y", "4Y", "5Y", "6Y", "7Y", "8Y",
    "9Y", "10Y", "11Y", "12Y", "15Y", "20Y", "25Y", "30Y",
]

# 국고채/통안채/신용채권 커브 블록 제목에서 상품명만 뽑기 위한 접두/치환 규칙
_CURVE_PREFIX = "시가평가 4사평균 "
_IRS_PREFIX = "원화 IRS 종합코드 "
_FX_CROSS_PREFIX = "이종통화 종합 "
_GROUP_RENAME = {"국고채권": "국고채", "통안증권": "통안채"}

# 원자재 제목 정리용: "(연결선물)" 꼬리표(끊겨도 매칭), "(LAST-DAY FIN.)" 표기, "YYYY-M" 만기월
_COMMODITY_TAIL_RE = re.compile(r"\s*\(연결선?물?\)?\s*$")
_COMMODITY_LASTDAY_RE = re.compile(r"\(LAST-DAY FIN\.\)")
_COMMODITY_DATE_RE = re.compile(r"\s*\d{4}-\d{1,2}\s*$")


def _clean_commodity_name(title: str) -> str:
    name = title.strip()
    name = _COMMODITY_TAIL_RE.sub("", name)
    name = _COMMODITY_LASTDAY_RE.sub("", name)
    name = _COMMODITY_DATE_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip()

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


def _normalize_credit_detail_tenor(label: str) -> str | None:
    """Info(크레딧) 서브헤더: '{N}(.5)?월이하(국고대비)' / '{N}(.5)?년이하(국고대비)'."""
    label = (label or "").strip().replace("(국고대비)", "")
    m = re.match(r"^(\d+(?:\.\d+)?)월이하$", label)
    if m:
        n = float(m.group(1))
        return f"{int(n) if n.is_integer() else n}M"
    m = re.match(r"^(\d+(?:\.\d+)?)년이하$", label)
    if m:
        n = float(m.group(1))
        return f"{int(n) if n.is_integer() else n}Y"
    return None


def _normalize_tenor_en(label: str) -> str | None:
    """IRS Zero/Forward curve 서브헤더 형식: '01M'~'11M', '01Y'~'50Y' (18M=1.5Y 특례)."""
    label = (label or "").strip().upper()
    m = re.match(r"^0*(\d+)M$", label)
    if m:
        months = int(m.group(1))
        return "1.5Y" if months == 18 else f"{months}M"
    m = re.match(r"^0*(\d+)Y$", label)
    if m:
        return f"{int(m.group(1))}Y"
    return None


def _block_to_group_tenor(title: str, sub, sheet_hint: str | None = None) -> tuple[str, str] | None:
    title = (title or "").strip()
    if sheet_hint == "크레딧상세":
        # Info(크레딧): 국고대비 스프레드(bp)를 훨씬 잘게 쪼갠 만기로 제공하는 별도 시트.
        # Info(국내금리)의 신용스프레드 그룹(예: "은행채AAA")과 이름이 겹치면 값 단위(bp vs %)가
        # 섞여버리므로 "크레딧_" 접두사로 완전히 분리된 그룹명을 쓴다.
        name = title[len(_CURVE_PREFIX):] if title.startswith(_CURVE_PREFIX) else title
        name = name.replace("(공모/무보증)", "").replace("(정부보증)", "정부보증").replace("금융채 ", "")
        name = re.sub(r"\s+", "", name)
        tenor = _normalize_credit_detail_tenor(sub)
        return (f"크레딧_{name}", tenor) if (name and tenor) else None
    if sheet_hint == "원자재":
        name = _clean_commodity_name(title)
        return (name, None) if name else None
    if sheet_hint == "주가":
        return (title, None) if title else None
    if title == "CP 4사평균 A1":
        # 같은 제목의 블록이 두 개(3개월물/1년물) 있어서 서브헤더로 구분해야 함
        sub_s = (sub or "").strip()
        if sub_s.startswith("3월"):
            return ("ABCP A1", "3M")
        if sub_s.startswith("1년"):
            return ("ABCP A1", "1Y")
        return None
    if title == "REPO 종합 1일물":
        return ("REPO", "1일")
    if title == "REPO 종합 7일물":
        return ("REPO", "7일")
    if title.startswith(_CURVE_PREFIX):
        name = title[len(_CURVE_PREFIX):].replace("(공모/무보증)", "")
        name = name.replace("금융채 ", "").replace(" ", "")
        name = _GROUP_RENAME.get(name, name)
        tenor = _normalize_tenor(sub)
        return (name, tenor) if tenor else None
    if title.startswith(_IRS_PREFIX):
        tenor = _normalize_tenor(title[len(_IRS_PREFIX):])
        return ("IRS", tenor) if tenor else None
    if title == "IRS ZERO CURVE KRWKRW":
        tenor = _normalize_tenor_en(sub)
        return ("IRS_ZERO", tenor) if tenor else None
    if title.startswith("IRS FORWARD CURVE KRWKRW"):
        tenor = _normalize_tenor_en(sub)
        return ("IRS_FWD3M", tenor) if tenor else None
    m = _FUTURES_RE.match(title)
    if m:
        if sub in (None, "일자"):
            return None
        field = "내재수익률" if sub == "선물내재수익률" else sub
        return (f"선물{m.group(1)}년", field)
    if "CD(91일물)" in title:
        return ("CD", "91D")
    if title == "한국:기준금리":
        return ("기준금리", None)
    # FX 시트: 환율은 만기 개념이 없는 단일 시계열이라 만기=None
    if title.startswith("달러인덱스"):
        return ("달러인덱스", None)
    if "USDKRW 스팟" in title:
        if title.startswith("서울외환"):
            return ("KRW", None)
        if title.startswith("TP NDF"):
            return ("NDF", None)
    if title.startswith(_FX_CROSS_PREFIX):
        code = title[len(_FX_CROSS_PREFIX):].strip()
        return (code, None) if code else None
    # 해외금리 시트: "{국가}(종합)? {N}년" 형태 (예: "미국(종합) 2년", "독일 10년")
    stripped = title.replace("(종합)", "").strip()
    if " " in stripped and stripped.rsplit(" ", 1)[-1].endswith("년"):
        country, tenor_str = stripped.rsplit(" ", 1)
        tenor = _normalize_tenor(tenor_str)
        if country and tenor:
            return (country, tenor)
    return None


# 시트 -> (sheet_hint). 같은 (날짜, 그룹, 만기)가 여러 시트에 있으면 앞선 시트 값을 쓴다(엑셀 시절과 동일한 우선순위).
_SHEET_ORDER = [
    (SHEET_DAILY, None),
    (SHEET_SHORT, None),
    (SHEET_FOREIGN, None),
    (SHEET_FX, None),
    (SHEET_COMMODITY, "원자재"),
    (SHEET_IRS_DETAIL, None),
    (SHEET_FUTURES, None),
    (SHEET_STOCK, "주가"),
    (SHEET_CREDIT_DETAIL, "크레딧상세"),
]

_EMPTY = pd.DataFrame(columns=["날짜", "그룹", "만기", "값"])


def _db_files() -> list[Path]:
    """읽을 DB 파일 목록(앞 -> 뒤로 우선순위가 높아짐). 로컬 market.db 가 있으면 그것만, 없으면 history + recent."""
    if DB_PATH.exists() and not os.environ.get("TRADINGBOARD_FORCE_SPLIT"):
        return [DB_PATH]
    if HISTORY_DB_PATH.exists() and RECENT_DB_PATH.exists():
        return [HISTORY_DB_PATH, RECENT_DB_PATH]
    return []


def data_version() -> float:
    """사용 중인 DB 파일들의 최신 수정시각 - 캐시 키/변경 감지용. DB가 없으면 0."""
    files = _db_files()
    return max((f.stat().st_mtime for f in files), default=0.0)


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _read_series(cons: list[sqlite3.Connection]) -> pd.DataFrame:
    frames = [pd.read_sql_query("SELECT column_name, sheet, block_title, field FROM series", c) for c in cons]
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset="column_name", keep="last")


def _read_wide(cons: list[sqlite3.Connection], cols: list[str]) -> pd.DataFrame:
    """여러 DB 의 market 을 날짜 기준으로 합친다(뒤쪽 DB 의 값이 우선, 비어 있으면 앞쪽 값 유지)."""
    merged = None
    for con in cons:
        have = {r[1] for r in con.execute("PRAGMA table_info(market)")}
        use = [c for c in cols if c in have]
        if not use:
            continue
        sel = ", ".join(f'"{c}"' for c in use)
        part = pd.read_sql_query(f"SELECT date, {sel} FROM market", con).set_index("date")
        merged = part if merged is None else part.combine_first(merged)
    return merged if merged is not None else pd.DataFrame()


def _load_sheet(cons: list[sqlite3.Connection], series: pd.DataFrame, sheet: str, hint: str | None) -> pd.DataFrame:
    col_map: dict[str, tuple[str, str | None]] = {}
    for r in series[series["sheet"] == sheet].itertuples(index=False):
        mapped = _block_to_group_tenor(r.block_title, r.field or None, hint)
        if mapped is not None:
            col_map[r.column_name] = mapped
    if not col_map:
        return _EMPTY
    wide = _read_wide(cons, list(col_map)).reset_index()
    if wide.empty:
        return _EMPTY
    long = wide.melt(id_vars="date", var_name="col", value_name="값").dropna(subset=["값"])
    long = long[long["값"] != 0]  # 0 은 "해당 만기 데이터 없음"
    groups = long["col"].map({c: g for c, (g, _t) in col_map.items()})
    tenors = long["col"].map({c: t for c, (_g, t) in col_map.items()})
    return pd.DataFrame({"날짜": long["date"], "그룹": groups, "만기": tenors, "값": long["값"]})


@st.cache_data(show_spinner="market.db 불러오는 중...")
def _load_raw_data_cached(data_ver: float) -> tuple[pd.DataFrame, bool]:
    cons = [_connect(f) for f in _db_files()]
    try:
        series = _read_series(cons)
        frames = [_load_sheet(cons, series, sheet, hint) for sheet, hint in _SHEET_ORDER]
    finally:
        for c in cons:
            c.close()

    df = pd.concat([f for f in frames if not f.empty], ignore_index=True).drop_duplicates(subset=["날짜", "그룹", "만기"])
    # 인도네시아 2Y는 최근 값이 여러 영업일째 그대로 고정된 상태(비정상 캐리포워드)로 보여 제외
    df = df[~((df["그룹"] == "인도네시아") & (df["만기"] == "2Y"))]
    df["날짜"] = pd.to_datetime(df["날짜"])
    _known_tenors = set(TENOR_ORDER) | set(IRS_ZERO_FWD_TENOR_ORDER)
    tenor_cat = [t for t in TENOR_ORDER if t in df["만기"].unique()] +                 [t for t in IRS_ZERO_FWD_TENOR_ORDER if t in df["만기"].unique() and t not in TENOR_ORDER] +                 [t for t in df["만기"].dropna().unique() if t not in _known_tenors]
    df["만기"] = pd.Categorical(df["만기"], categories=tenor_cat, ordered=True)
    return df.sort_values(["그룹", "날짜", "만기"]).reset_index(drop=True), False


def load_raw_data() -> tuple[pd.DataFrame, bool]:
    """
    (long-format DataFrame [날짜, 그룹, 만기, 값], is_sample) 반환.
    DB 가 없으면 빈 데이터프레임 + is_sample=True.
    DB 가 바뀌지 않는 한(data_version 기준) 캐시된 결과를 재사용한다.
    """
    if not _db_files():
        return _EMPTY.copy(), True
    return _load_raw_data_cached(data_version())


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
