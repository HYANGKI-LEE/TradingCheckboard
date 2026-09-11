"""
채권/IRS 커브 데이터 로더.

실데이터 연동 전까지는 인포맥스 엑셀과 동일한 형식(long format)의 샘플 데이터를 생성해서
대시보드가 바로 동작하도록 한다. 실제 인포맥스 파일을 받으면 이 파일의
EXCEL_PATH / SHEET_NAME / 컬럼명만 실제 구조에 맞게 바꾸면 된다.

기대하는 엑셀 구조 (long format, 시트 1개):
    날짜        | 만기  | 국고채금리 | IRS금리
    2026-09-10  | 1Y   | 2.85      | 2.95
    2026-09-10  | 3Y   | 2.80      | 2.92
    ...

- 날짜: 각 갱신 시점 (인포맥스에서 값이 갱신될 때마다 한 줄씩 쌓는 방식을 가정)
- 만기: TENOR_ORDER 에 있는 라벨 중 하나
- 국고채금리 / IRS금리: %, 둘 중 하나가 비어있어도 됨 (예: IRS는 1Y~10Y만 유동적)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
EXCEL_PATH = DATA_DIR / "curve_data.xlsx"
SHEET_NAME = "커브데이터"

TENOR_ORDER = ["3M", "6M", "9M", "1Y", "1.5Y", "2Y", "3Y", "4Y", "5Y", "7Y", "10Y", "20Y", "30Y", "50Y"]
IRS_LIQUID_TENORS = {"1Y", "1.5Y", "2Y", "3Y", "4Y", "5Y", "7Y", "10Y"}

REQUIRED_COLUMNS = ["날짜", "만기", "국고채금리", "IRS금리"]


def _apply_tenor_order(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["만기"] = pd.Categorical(df["만기"], categories=TENOR_ORDER, ordered=True)
    df["날짜"] = pd.to_datetime(df["날짜"])
    return df.sort_values(["날짜", "만기"]).reset_index(drop=True)


def generate_sample_data(n_days: int = 60, seed: int = 42) -> pd.DataFrame:
    """실제 인포맥스 파일이 들어오기 전까지 쓰는 더미 데이터."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n_days)

    base_ktb = {
        "3M": 2.55, "6M": 2.58, "9M": 2.62, "1Y": 2.65, "1.5Y": 2.70, "2Y": 2.75,
        "3Y": 2.80, "4Y": 2.85, "5Y": 2.88, "7Y": 2.95, "10Y": 3.05, "20Y": 3.15,
        "30Y": 3.10, "50Y": 3.05,
    }
    irs_spread = {  # IRS - 국고채, bp 단위 감으로 생성
        "1Y": 8, "1.5Y": 7, "2Y": 6, "3Y": 4, "4Y": 3, "5Y": 2, "7Y": 0, "10Y": -3,
    }

    rows = []
    level_drift = 0.0
    for d in dates:
        level_drift += rng.normal(0, 0.01)
        for tenor in TENOR_ORDER:
            noise = rng.normal(0, 0.02)
            ktb = base_ktb[tenor] + level_drift + noise
            irs = None
            if tenor in IRS_LIQUID_TENORS:
                irs = ktb + irs_spread[tenor] / 100 + rng.normal(0, 0.015)
            rows.append({"날짜": d, "만기": tenor, "국고채금리": round(ktb, 3),
                         "IRS금리": round(irs, 3) if irs is not None else np.nan})

    return _apply_tenor_order(pd.DataFrame(rows))


def load_curve_data() -> tuple[pd.DataFrame, bool]:
    """
    (데이터, is_sample) 반환. 실제 EXCEL_PATH 가 있으면 그걸 읽고,
    없으면 샘플 데이터를 생성해서 반환한다 (is_sample=True).
    """
    if EXCEL_PATH.exists():
        df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{EXCEL_PATH.name} 에 필요한 컬럼이 없습니다: {missing}")
        return _apply_tenor_order(df), False

    return generate_sample_data(), True


def latest_curve(df: pd.DataFrame) -> pd.DataFrame:
    """가장 최근 날짜의 만기별 스냅샷 (커브 한 가닥)."""
    latest_date = df["날짜"].max()
    return df[df["날짜"] == latest_date].sort_values("만기")


def spread_series(df: pd.DataFrame) -> pd.DataFrame:
    """만기별 IRS-국고채 스프레드(bp) 시계열."""
    out = df.copy()
    out["스프레드_bp"] = (out["IRS금리"] - out["국고채금리"]) * 100
    return out
