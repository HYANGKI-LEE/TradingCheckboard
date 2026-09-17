from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from data_loader import (
    CREDIT_GROUPS_ORDER,
    FOREIGN_COUNTRIES_ORDER,
    COMMODITY_ORDER,
    IRS_ZERO_FWD_TENOR_ORDER,
    load_raw_data,
    latest_curve,
    curve_history,
    credit_spread,
)

st.set_page_config(page_title="채권/IRS 트레이딩 대시보드", layout="wide")

df, is_sample = load_raw_data()

if is_sample:
    st.title("📈 채권/IRS 트레이딩 대시보드")
    st.warning(
        "`data/RawData.xlsx` 파일을 찾을 수 없습니다. 인포맥스 RawData 파일을 `data/RawData.xlsx` 로 넣어주세요.",
        icon="⚠️",
    )
    st.stop()

CURVE_TENORS = ["1Y", "2Y", "3Y", "4Y", "5Y", "10Y", "20Y", "30Y"]
IRS_TENORS = [t for t in df.loc[df["그룹"] == "IRS", "만기"].dropna().unique()]
IRS_TENORS = sorted(IRS_TENORS, key=lambda t: (float(t[:-1]) if t.endswith("M") else float(t[:-1]) * 12))

DOMESTIC_RATE_TENORS = ["2Y", "3Y", "10Y", "30Y"]
DOMESTIC_SPREADS = [("3Y", "1Y"), ("5Y", "3Y"), ("10Y", "3Y"), ("30Y", "10Y")]
MA_WINDOWS = [20, 60, 120, 200]


def _with_ma(series: pd.Series) -> dict:
    return {f"MA{w}": series.rolling(window=w, min_periods=w).mean() for w in MA_WINDOWS}


PERIOD_PRESETS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "10Y", "MTD", "QTD", "YTD", "MAX", "설정"]


def _preset_to_start(preset: str, min_date, max_date):
    if preset == "MTD":
        start = max_date.replace(day=1)
    elif preset == "QTD":
        q_start_month = (max_date.month - 1) // 3 * 3 + 1
        start = max_date.replace(month=q_start_month, day=1)
    elif preset == "YTD":
        start = max_date.replace(month=1, day=1)
    elif preset == "MAX":
        start = min_date
    else:
        n = int(preset[:-1])
        offset = pd.DateOffset(months=n) if preset.endswith("M") else pd.DateOffset(years=n)
        start = (pd.Timestamp(max_date) - offset).date()
    return max(start, min_date)


def period_selector(min_date, max_date, key_prefix: str, default: str = "5Y"):
    """기간 프리셋(1M~10Y, MTD/QTD/YTD/MAX) + 직접설정(캘린더) 선택 위젯. (시작일, 종료일) 반환."""
    preset = st.segmented_control(
        "기간", PERIOD_PRESETS, default=default, key=f"{key_prefix}_period",
    ) or default

    if preset == "설정":
        custom_default_start = _preset_to_start(default, min_date, max_date)
        custom_range = st.date_input(
            "직접 설정", value=(custom_default_start, max_date),
            min_value=min_date, max_value=max_date, key=f"{key_prefix}_custom_range",
        )
        if isinstance(custom_range, tuple) and len(custom_range) == 2:
            return custom_range
        return custom_default_start, max_date

    return _preset_to_start(preset, min_date, max_date), max_date


MA_COLORS = {20: "#E67E22", 60: "#27AE60", 120: "#2980B9", 200: "#8E44AD"}


def _plot_with_ma(view: pd.DataFrame, title: str, yaxis_title: str, name: str, key: str, avg_line: float | None = None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=view["날짜"], y=view["값"], mode="lines", name=name,
                              line=dict(width=3, color="black")))
    for w in MA_WINDOWS:
        fig.add_trace(go.Scatter(x=view["날짜"], y=view[f"MA{w}"], mode="lines",
                                  name=f"MA{w}", line=dict(width=1.5, color=MA_COLORS[w])))
    if avg_line is not None:
        fig.add_hline(y=avg_line, line_dash="dash", line_color="black", line_width=1.2,
                      annotation_text=f"장기평균 {avg_line:.1f}", annotation_position="top left",
                      annotation_font_size=10)
    fig.update_layout(title=title, height=360, yaxis_title=yaxis_title,
                       legend=dict(orientation="h", y=-0.25), margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True, key=key)


def _tenor_history_view(group: str, tenor: str, start_date, end_date) -> pd.DataFrame:
    hist = curve_history(df, group, tenor).copy()
    hist = hist.assign(**_with_ma(hist["값"]))
    return hist[(hist["날짜"].dt.date >= start_date) & (hist["날짜"].dt.date <= end_date)]


def _tenor_spread_view(group: str, long_t: str, short_t: str, start_date, end_date) -> pd.DataFrame:
    """같은 그룹 내에서 만기간 스프레드 (장기 - 단기, bp)."""
    a = curve_history(df, group, long_t)[["날짜", "값"]].rename(columns={"값": "장기"})
    b = curve_history(df, group, short_t)[["날짜", "값"]].rename(columns={"값": "단기"})
    merged = a.merge(b, on="날짜", how="inner").sort_values("날짜")
    merged["값"] = (merged["장기"] - merged["단기"]) * 100
    merged = merged.assign(**_with_ma(merged["값"]))
    return merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]


def _cross_group_spread_view(group_a: str, group_b: str, tenor: str, start_date, end_date) -> pd.DataFrame:
    """서로 다른 그룹(국가/상품군) 간 같은 만기 스프레드 (group_a - group_b, bp)."""
    spread_df = credit_spread(df, group_a, group_b)
    spread_df = spread_df[spread_df["만기"] == tenor].sort_values("날짜").rename(columns={"스프레드_bp": "값"})
    spread_df = spread_df.assign(**_with_ma(spread_df["값"]))
    return spread_df[(spread_df["날짜"].dt.date >= start_date) & (spread_df["날짜"].dt.date <= end_date)]


# 회사채/여전채는 등급별 커브가 여러 개라 대표로 AA- 사용. "여전채"는 원본 데이터의 "기타금융채" 그룹으로 매핑함
# (카드채는 별도 그룹이라 "여전채"는 카드채를 제외한 여신전문금융회사채 커브로 간주) - 다르면 알려주세요.
DOMESTIC_CHANGE_SERIES = [
    ("통안2년", "통안채", "2Y"),
    ("국고3년", "국고채", "3Y"),
    ("국고10년", "국고채", "10Y"),
    ("IRS1년", "IRS", "1Y"),
    ("IRS2년", "IRS", "2Y"),
    ("회사채AA-2년", "회사채AA-", "2Y"),
    ("여전채AA-2년", "기타금융채AA-", "2Y"),
]


def _change_since_start_view(group: str, tenor: str, start_date, end_date) -> pd.DataFrame:
    """선택 기간 시작일 값 대비 변동(bp)."""
    hist = curve_history(df, group, tenor)
    hist = hist[(hist["날짜"].dt.date >= start_date) & (hist["날짜"].dt.date <= end_date)].sort_values("날짜").copy()
    if hist.empty:
        return hist.assign(변동=[])
    hist["변동"] = (hist["값"] - hist["값"].iloc[0]) * 100
    return hist


def _plot_change_multi(series_list: list[tuple[str, str, str]], start_date, end_date, title: str, key: str):
    fig = go.Figure()
    for label, group, tenor in series_list:
        view = _change_since_start_view(group, tenor, start_date, end_date)
        if view.empty:
            continue
        fig.add_trace(go.Scatter(x=view["날짜"], y=view["변동"], mode="lines+markers", name=label,
                                  line=dict(width=2), marker=dict(size=4)))
    fig.add_hline(y=0, line_color="gray", line_width=1)
    fig.update_layout(title=title, yaxis_title="(bp)", height=460,
                       legend=dict(orientation="h", y=-0.22), margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True, key=key)


# ================================================================ 국내금리
def page_domestic_rate():
    st.title("🏛️ 국내금리")

    govt_dates = df.loc[df["그룹"] == "국고채", "날짜"]
    min_date, max_date = govt_dates.min().date(), govt_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="domestic", default="1Y")

    tab_rates, tab_change, tab_spread, tab_futures = st.tabs(["Rates", "변동", "스프레드", "선물"])

    with tab_rates:
        cols = st.columns(2)
        for i, tenor in enumerate(DOMESTIC_RATE_TENORS):
            view = _tenor_history_view("국고채", tenor, start_date, end_date)
            with cols[i % 2]:
                _plot_with_ma(view, f"국고채 {tenor}", "금리 (%)", f"국고채 {tenor}", key=f"rate_{tenor}")

    with tab_change:
        change_start, change_end = period_selector(min_date, max_date, key_prefix="domestic_change", default="YTD")
        _plot_change_multi(DOMESTIC_CHANGE_SERIES, change_start, change_end,
                            "주요금리 변동 추이", key="domestic_change_chart")

    with tab_spread:
        cols2 = st.columns(2)
        for i, (long_t, short_t) in enumerate(DOMESTIC_SPREADS):
            view = _tenor_spread_view("국고채", long_t, short_t, start_date, end_date)
            label = f"{long_t}-{short_t}"
            with cols2[i % 2]:
                _plot_with_ma(view, f"국고채 {label} 스프레드", "bp", label, key=f"spread_{label}")

    with tab_futures:
        for label, futures_group in [("3년", "선물3년"), ("5년", "선물5년"), ("10년", "선물10년"), ("30년", "선물30년")]:
            if df.loc[df["그룹"] == futures_group].empty:
                continue
            st.markdown(f"#### {label}국채선물")
            price_view = _tenor_history_view(futures_group, "현재가", start_date, end_date)
            richness_view = _futures_richness_bp(futures_group)
            richness_view = richness_view.assign(**_with_ma(richness_view["값"]))
            richness_view = richness_view[(richness_view["날짜"].dt.date >= start_date) &
                                           (richness_view["날짜"].dt.date <= end_date)]
            colf1, colf2 = st.columns(2)
            with colf1:
                _plot_with_ma(price_view, f"{label}국채선물 가격", "가격", f"{label}국채선물", key=f"futures_price_{futures_group}")
            with colf2:
                _plot_with_ma(richness_view, f"{label}국채선물 저평", "bp", f"{label}국채선물 저평",
                              key=f"futures_rich_{futures_group}")
            _chart_gap()


IRS_SPREAD_PAIRS = [
    ("9M", "6M"), ("1Y", "6M"), ("1.5Y", "6M"), ("2Y", "6M"),
    ("1Y", "9M"), ("1.5Y", "9M"), ("2Y", "9M"),
    ("1.5Y", "1Y"), ("2Y", "1Y"), ("3Y", "1Y"),
    ("2Y", "1.5Y"), ("3Y", "1.5Y"),
    ("3Y", "2Y"), ("5Y", "2Y"), ("10Y", "2Y"),
    ("5Y", "3Y"),
    ("5Y", "4Y"),
    ("6Y", "5Y"), ("10Y", "5Y"),
    ("10Y", "9Y"),
    ("12Y", "10Y"),
]

IRS_BUTTERFLIES = [
    # 2년 이하 단기구간: 가능한 조합 전부
    ("6M", "9M", "1Y"), ("6M", "9M", "1.5Y"), ("6M", "9M", "2Y"),
    ("6M", "1Y", "1.5Y"), ("6M", "1Y", "2Y"), ("6M", "1.5Y", "2Y"),
    ("9M", "1Y", "1.5Y"), ("9M", "1Y", "2Y"), ("9M", "1.5Y", "2Y"),
    ("1Y", "1.5Y", "2Y"),
    # 3년 이상: 우선 지정된 조합만
    ("1Y", "2Y", "3Y"), ("2Y", "3Y", "4Y"), ("3Y", "4Y", "5Y"),
    ("1Y", "3Y", "10Y"), ("2Y", "5Y", "10Y"), ("3Y", "5Y", "10Y"), ("10Y", "20Y", "30Y"),
]


def _irs_butterfly_view(short_t: str, mid_t: str, long_t: str, start_date, end_date) -> pd.DataFrame:
    """나비형 스프레드 = IRS(단기) + IRS(장기) - IRS(중기), bp. 예: 3-5-10 = 3Y+10Y-5Y."""
    a = curve_history(df, "IRS", short_t)[["날짜", "값"]].rename(columns={"값": "short"})
    b = curve_history(df, "IRS", mid_t)[["날짜", "값"]].rename(columns={"값": "mid"})
    c = curve_history(df, "IRS", long_t)[["날짜", "값"]].rename(columns={"값": "long"})
    merged = a.merge(b, on="날짜", how="inner").merge(c, on="날짜", how="inner").sort_values("날짜")
    merged["값"] = (merged["short"] + merged["long"] - merged["mid"]) * 100
    merged = merged.assign(**_with_ma(merged["값"]))
    return merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]


# ================================================================ IRS (Par/스프레드/Zero/Fwd/버터플라이)
def page_irs_detail():
    st.title("🔁 IRS")

    irs_dates = df.loc[df["그룹"] == "IRS", "날짜"]
    min_date, max_date = irs_dates.min().date(), irs_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="irs_detail", default="1Y")
    available_tenors = set(df.loc[df["그룹"] == "IRS", "만기"].dropna().astype(str).unique())

    tab_par, tab_spread, tab_zero, tab_fwd, tab_fly = st.tabs(
        ["Par rate", "스프레드", "Zero rate", "Fwd rate", "버터플라이"]
    )

    for tab, label, group in [(tab_par, "Par rate", "IRS"), (tab_zero, "Zero rate", "IRS_ZERO"),
                               (tab_fwd, "Fwd rate", "IRS_FWD3M")]:
        with tab:
            avail = set(df.loc[df["그룹"] == group, "만기"].dropna().astype(str).unique())
            tenors = [t for t in IRS_ZERO_FWD_TENOR_ORDER if t in avail]
            cols = st.columns(3)
            for i, tenor in enumerate(tenors):
                view = _tenor_history_view(group, tenor, start_date, end_date)
                with cols[i % 3]:
                    _plot_with_ma(view, f"{label} {tenor}", "%", f"{label} {tenor}",
                                  key=f"irsdetail_{group}_{tenor}")

    with tab_spread:
        st.markdown("#### 기준금리")
        base_rate = df[df["그룹"] == "기준금리"][["날짜", "값"]].sort_values("날짜")
        base_rate = base_rate[(base_rate["날짜"].dt.date >= start_date) & (base_rate["날짜"].dt.date <= end_date)]
        fig_base = go.Figure()
        fig_base.add_trace(go.Scatter(x=base_rate["날짜"], y=base_rate["값"], mode="lines", name="기준금리",
                                       line=dict(color="gray", shape="hv", width=2)))
        fig_base.update_layout(height=280, yaxis_title="%", margin=dict(t=20))
        st.plotly_chart(fig_base, use_container_width=True, key="irs_spread_base_rate")

        _chart_gap()
        cols = st.columns(3)
        i = 0
        for long_t, short_t in IRS_SPREAD_PAIRS:
            if long_t not in available_tenors or short_t not in available_tenors:
                continue
            view = _tenor_spread_view("IRS", long_t, short_t, start_date, end_date)
            label = f"{short_t}-{long_t}"
            with cols[i % 3]:
                _plot_with_ma(view, f"IRS {label}", "bp", label, key=f"irs_spread_{label}")
            i += 1

        _chart_gap()
        st.markdown("#### 커스텀 스프레드")
        tenor_options = [t for t in IRS_ZERO_FWD_TENOR_ORDER if t in available_tenors]
        col_a, col_b = st.columns(2)
        with col_a:
            tenor_a = st.selectbox("만기 A", tenor_options,
                                    index=tenor_options.index("3Y") if "3Y" in tenor_options else 0,
                                    key="irs_custom_a")
        with col_b:
            tenor_b = st.selectbox("만기 B", tenor_options,
                                    index=tenor_options.index("1Y") if "1Y" in tenor_options else 0,
                                    key="irs_custom_b")
        if tenor_a != tenor_b:
            custom_view = _tenor_spread_view("IRS", tenor_a, tenor_b, start_date, end_date)
            _plot_with_ma(custom_view, f"IRS {tenor_a}-{tenor_b}", "bp", f"{tenor_a}-{tenor_b}",
                          key="irs_custom_spread")
        else:
            st.caption("서로 다른 만기 2개를 선택해주세요.")

    with tab_fly:
        cols = st.columns(3)
        i = 0
        for short_t, mid_t, long_t in IRS_BUTTERFLIES:
            if not all(t in available_tenors for t in (short_t, mid_t, long_t)):
                continue
            view = _irs_butterfly_view(short_t, mid_t, long_t, start_date, end_date)
            label = f"{short_t}-{mid_t}-{long_t}"
            with cols[i % 3]:
                _plot_with_ma(view, f"IRS 버터플라이 {label}", "bp", label, key=f"irs_fly_{label}")
            i += 1


FOREIGN_RATE_PREFERRED = ["2Y", "10Y", "30Y"]
FOREIGN_RATE_FALLBACK = ["3Y", "10Y", "30Y"]
CROSS_COUNTRY_SPREADS = [
    ("한국", "미국"), ("미국", "독일"), ("미국", "영국"),
    ("영국", "독일"), ("이탈리아", "독일"), ("한국", "호주"),
]

COUNTRY_ISO2 = {
    "한국": "kr", "미국": "us", "독일": "de", "영국": "gb", "프랑스": "fr",
    "이탈리아": "it", "일본": "jp", "호주": "au", "캐나다": "ca", "인도": "in",
    "인도네시아": "id", "브라질": "br", "멕시코": "mx", "남아공": "za",
}


def _flag_html(country: str, size: int = 20) -> str:
    """국기 이모지는 Windows/Plotly 조합에서 깨지는 경우가 있어 실제 이미지로 표시."""
    iso = COUNTRY_ISO2.get(country)
    if not iso:
        return ""
    return f'<img src="https://flagcdn.com/{size}x{int(size * 0.75)}/{iso}.png" style="vertical-align:middle;margin-right:6px">'


def _chart_gap():
    st.write("")
    st.write("")


def _foreign_group_key(display_name: str) -> str:
    return "국고채" if display_name == "한국" else display_name


def _effective_latest_idx(vals: list) -> int:
    """
    마지막 몇 개 값이 그 앞 값과 똑같이 반복되면(당일 미갱신/캐리포워드) 그 구간을 건너뛰고
    실제로 값이 바뀐 가장 최근 시점의 인덱스를 반환한다.
    """
    idx = len(vals) - 1
    while idx > 0 and vals[idx] == vals[idx - 1]:
        idx -= 1
    return idx


def _foreign_rate_change_table(tenor: str = "10Y") -> pd.DataFrame:
    rows = []
    for country in FOREIGN_COUNTRIES_ORDER:
        hist = curve_history(df, country, tenor).sort_values("날짜").reset_index(drop=True)
        if len(hist) < 2:
            continue
        idx = _effective_latest_idx(hist["값"].tolist())
        latest_date = hist.loc[idx, "날짜"].date()
        latest_val = hist.loc[idx, "값"]
        prev_val = hist.loc[idx - 1, "값"] if idx > 0 else None
        min_d = hist["날짜"].min().date()
        hist_upto = hist.loc[:idx]  # 유효 최신일 이후 데이터는 변동 계산에서 제외

        def chg(ref_date):
            prior = hist_upto[hist_upto["날짜"].dt.date <= ref_date]
            return round((latest_val - prior.iloc[-1]["값"]) * 100, 1) if not prior.empty else None

        rows.append({
            "국가": country,
            "기준일": latest_date,
            "현재가(%)": round(latest_val, 3),
            "전일대비(bp)": round((latest_val - prev_val) * 100, 1) if prev_val is not None else None,
            "1W(bp)": chg(latest_date - pd.Timedelta(days=7)),
            "MTD(bp)": chg(_preset_to_start("MTD", min_d, latest_date)),
            "1M(bp)": chg(_preset_to_start("1M", min_d, latest_date)),
            "QTD(bp)": chg(_preset_to_start("QTD", min_d, latest_date)),
            "YTD(bp)": chg(_preset_to_start("YTD", min_d, latest_date)),
        })
    return pd.DataFrame(rows)


# ================================================================ 해외금리
def page_foreign_rate():
    st.title("🌍 해외금리")

    foreign_dates = df.loc[df["그룹"].isin(FOREIGN_COUNTRIES_ORDER), "날짜"]
    min_date, max_date = foreign_dates.min().date(), foreign_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="foreign", default="1Y")

    tab_change, tab_rates, tab_spread_period, tab_spread_country = st.tabs(
        ["변동", "Rates", "스프레드(기간)", "스프레드(국가간)"]
    )

    with tab_change:
        st.caption("만기: 10Y 기준")
        change_table = _foreign_rate_change_table("10Y")
        metric_options = ["전일대비(bp)", "1W(bp)", "MTD(bp)", "1M(bp)", "QTD(bp)", "YTD(bp)"]

        col_table, col_bar = st.columns([2, 3])
        with col_table:
            st.dataframe(change_table, use_container_width=True, hide_index=True)
        with col_bar:
            metric = st.segmented_control("막대그래프 기준", metric_options, default="전일대비(bp)", key="foreign_change_metric") \
                or "전일대비(bp)"
            bar_df = change_table[["국가", metric]].dropna().sort_values(metric, ascending=True)
            fig_bar = go.Figure(go.Bar(
                x=bar_df[metric], y=bar_df["국가"], orientation="h",
                marker_color="#159895", text=bar_df[metric], texttemplate="%{text:.1f}",
                textposition="outside",
            ))
            fig_bar.update_layout(title=metric, height=max(320, 28 * len(bar_df)), margin=dict(t=40, r=40))
            st.plotly_chart(fig_bar, use_container_width=True, key="foreign_change_bar")

    with tab_rates:
        for country in FOREIGN_COUNTRIES_ORDER:
            available = set(df.loc[df["그룹"] == country, "만기"].dropna().astype(str).unique())
            target = FOREIGN_RATE_PREFERRED if "2Y" in available else FOREIGN_RATE_FALLBACK
            target = [t for t in target if t in available]
            if not target:
                continue
            st.markdown(f"#### {_flag_html(country, 24)}{country}", unsafe_allow_html=True)
            cols = st.columns(3)
            for i, tenor in enumerate(target):
                view = _tenor_history_view(country, tenor, start_date, end_date)
                with cols[i]:
                    _plot_with_ma(view, f"{country} {tenor}", "금리 (%)", f"{country} {tenor}",
                                  key=f"foreign_rate_{country}_{tenor}")
            _chart_gap()

    with tab_spread_period:
        cols2 = st.columns(3)
        idx = 0
        for country in FOREIGN_COUNTRIES_ORDER:
            available = set(df.loc[df["그룹"] == country, "만기"].dropna().astype(str).unique())
            short_t = "2Y" if "2Y" in available else ("3Y" if "3Y" in available else None)
            if short_t is None or "10Y" not in available:
                continue
            view = _tenor_spread_view(country, "10Y", short_t, start_date, end_date)
            label = f"10Y-{short_t}"
            with cols2[idx % 3]:
                st.markdown(f"{_flag_html(country, 16)}**{country}**", unsafe_allow_html=True)
                _plot_with_ma(view, f"{country} {label}", "bp", f"{country} {label}",
                              key=f"foreign_spread_{country}")
            idx += 1
            if idx % 3 == 0:
                _chart_gap()

    with tab_spread_country:
        cols3 = st.columns(3)
        for i, (a, b) in enumerate(CROSS_COUNTRY_SPREADS):
            view = _cross_group_spread_view(_foreign_group_key(a), _foreign_group_key(b), "10Y", start_date, end_date)
            label = f"{a}-{b}"
            with cols3[i % 3]:
                st.markdown(f"{_flag_html(a, 16)}{a} − {_flag_html(b, 16)}{b}", unsafe_allow_html=True)
                _plot_with_ma(view, f"{label} (10Y)", "bp", label, key=f"cross_spread_{label}")


FX_FLAGS = {
    "KRW": "🇰🇷", "NDF": "🇰🇷", "달러인덱스": "💵", "JPY": "🇯🇵", "EUR": "🇪🇺", "GBP": "🇬🇧",
    "EURCHF": "🇪🇺🇨🇭", "EURGBP": "🇪🇺🇬🇧", "CHF": "🇨🇭", "AUD": "🇦🇺", "AUDNZD": "🇦🇺🇳🇿", "AUDCAD": "🇦🇺🇨🇦",
    "CNY": "🇨🇳", "BRL": "🇧🇷", "INR": "🇮🇳", "IDR": "🇮🇩", "ZAR": "🇿🇦", "TRY": "🇹🇷", "MXN": "🇲🇽", "RUB": "🇷🇺",
    "BRLKRW": "🇧🇷🇰🇷", "MXNKRW": "🇲🇽🇰🇷", "INRKRW": "🇮🇳🇰🇷", "IDRKRW": "🇮🇩🇰🇷",
}

# (표시 그룹, KRW와의 크로스로 직접 계산해야 하는지 여부)
FX_ORDER = [
    ("KRW", False), ("NDF", False), ("달러인덱스", False), ("JPY", False), ("EUR", False), ("GBP", False),
    ("EURCHF", False), ("EURGBP", False), ("CHF", False), ("AUD", False), ("AUDNZD", False), ("AUDCAD", False),
    ("CNY", False), ("BRL", False), ("INR", False), ("IDR", False), ("ZAR", False), ("TRY", False),
    ("MXN", False), ("RUB", False),
    ("BRLKRW", True), ("MXNKRW", True), ("INRKRW", True), ("IDRKRW", True),
]


def _series_history_view(group: str, start_date, end_date) -> pd.DataFrame:
    hist = df[df["그룹"] == group][["날짜", "값"]].sort_values("날짜").copy()
    hist = hist.assign(**_with_ma(hist["값"]))
    return hist[(hist["날짜"].dt.date >= start_date) & (hist["날짜"].dt.date <= end_date)]


def _fx_cross_krw_view(ccy_group: str, start_date, end_date) -> pd.DataFrame:
    """KRW 환율 / 해당 통화(USD 대비) 환율로 계산하는 교차환율 (예: BRLKRW = USDKRW / USDBRL)."""
    krw = df[df["그룹"] == "KRW"][["날짜", "값"]].rename(columns={"값": "usdkrw"})
    ccy = df[df["그룹"] == ccy_group.replace("KRW", "")][["날짜", "값"]].rename(columns={"값": "usdccy"})
    merged = krw.merge(ccy, on="날짜", how="inner").sort_values("날짜")
    merged["값"] = merged["usdkrw"] / merged["usdccy"]
    merged = merged.assign(**_with_ma(merged["값"]))
    return merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]


# ================================================================ FX
def page_fx():
    st.title("💱 FX")

    fx_groups = [g for g, is_cross in FX_ORDER if not is_cross]
    fx_dates = df.loc[df["그룹"].isin(fx_groups), "날짜"]
    min_date, max_date = fx_dates.min().date(), fx_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="fx", default="1Y")

    cols = st.columns(3)
    for i, (group, is_cross) in enumerate(FX_ORDER):
        view = _fx_cross_krw_view(group, start_date, end_date) if is_cross else _series_history_view(group, start_date, end_date)
        flag = FX_FLAGS.get(group, "")
        with cols[i % 3]:
            _plot_with_ma(view, f"{flag} {group}", "환율", f"{flag} {group}", key=f"fx_{group}")
        if (i + 1) % 3 == 0:
            _chart_gap()


COMMODITY_EMOJI = {
    "WTI": "🛢️", "브렌트": "🛢️", "두바이유": "🛢️", "천연가스": "🔥", "팔라듐": "⚪", "백금": "⚪",
    "블룸버그 상품 지수": "📊", "에탄올": "⛽", "KC HRW 밀": "🌾", "다우 존스 부동산": "🏠",
    "미니 옥수수": "🌽", "미니 콩": "🫘", "미니 소맥": "🌾", "옥수수": "🌽", "대두유": "🛢️",
    "대두박": "🌱", "귀리": "🌾", "30 DAY FEDERAL FUNDS": "💵", "쌀": "🍚", "대두": "🫘",
    "시카고 SRW 밀": "🌾", "버터": "🧈", "치즈": "🧀", "3등급 우유": "🥛", "4등급 우유": "🥛",
    "비육우": "🐄", "무지방 건조우유": "🥛", "돈육": "🐖", "생우": "🐂", "코코아": "🍫",
    "면화": "🧵", "미국달러지수": "💵", "커피": "☕", "오렌지주스": "🍊", "설탕": "🍬",
    "금": "🥇", "은": "🥈", "구리": "🟠", "알루미늄": "⚙️", "금은Ratio": "⚖️",
}


COMMODITY_CATEGORIES = {
    "🔥 에너지": ["WTI", "브렌트", "두바이유", "천연가스"],
    "🥇 귀금속": ["금", "은", "금은Ratio", "구리", "백금", "팔라듐"],
    "🌾 음식": [
        "KC HRW 밀", "시카고 SRW 밀", "옥수수", "쌀", "귀리", "대두",
        "버터", "치즈", "돈육", "생우", "코코아", "커피", "설탕",
    ],
    "📊 기타": ["블룸버그 상품 지수", "다우 존스 부동산", "30 DAY FEDERAL FUNDS", "면화", "미국달러지수", "알루미늄"],
}


# ================================================================ 원자재
def _commodity_ratio_view(a_group: str, b_group: str, start_date, end_date) -> pd.DataFrame:
    a = df[df["그룹"] == a_group][["날짜", "값"]].rename(columns={"값": "a"})
    b = df[df["그룹"] == b_group][["날짜", "값"]].rename(columns={"값": "b"})
    merged = a.merge(b, on="날짜", how="inner").sort_values("날짜")
    merged["값"] = merged["a"] / merged["b"]
    merged = merged.assign(**_with_ma(merged["값"]))
    return merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]


def page_commodity():
    st.title("🛢️ 원자재")

    commodity_dates = df.loc[df["그룹"].isin(COMMODITY_ORDER), "날짜"]
    min_date, max_date = commodity_dates.min().date(), commodity_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="commodity", default="1Y")

    for category, groups in COMMODITY_CATEGORIES.items():
        available = [g for g in groups if g == "금은Ratio" or not df.loc[df["그룹"] == g].empty]
        if not available:
            continue
        st.subheader(category)
        cols = st.columns(3)
        for i, group in enumerate(available):
            emoji = COMMODITY_EMOJI.get(group, "")
            if group == "금은Ratio":
                view = _commodity_ratio_view("금", "은", start_date, end_date)
                yaxis_title = "Ratio"
            else:
                view = _series_history_view(group, start_date, end_date)
                yaxis_title = "가격"
            with cols[i % 3]:
                _plot_with_ma(view, f"{emoji} {group}", yaxis_title, f"{emoji} {group}", key=f"commodity_{group}")
        _chart_gap()


STOCK_INDEX_ORDER = [
    "KOSPI", "KOSDAQ", "다우 산업", "S&P 500", "나스닥 종합", "나스닥 100",
    "니케이 225", "상해종합", "CSI 300", "대만 가권", "항셍", "독일 DAX30", "프랑스 CAC40", "다우 종합",
]


def _yield_gap_data(start_date, end_date) -> pd.DataFrame:
    per = df[df["그룹"] == "한국:PER-KRX:트레일링"][["날짜", "값"]].rename(columns={"값": "per"})
    ktb3 = curve_history(df, "국고채", "3Y")[["날짜", "값"]].rename(columns={"값": "국고채 3년"})
    merged = per.merge(ktb3, on="날짜", how="inner").sort_values("날짜")
    merged["1/PER"] = (1 / merged["per"]) * 100
    merged["갭"] = merged["1/PER"] - merged["국고채 3년"]
    return merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]


# ================================================================ 주식
def page_stock():
    st.title("📈 주식")

    stock_dates = df.loc[df["그룹"] == "KOSPI", "날짜"]
    min_date, max_date = stock_dates.min().date(), stock_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="stock", default="1Y")

    tab_yieldgap, tab_indices = st.tabs(["Yield Gap", "주가추이"])

    with tab_indices:
        available = [g for g in STOCK_INDEX_ORDER if not df.loc[df["그룹"] == g].empty]
        cols_idx = st.columns(3)
        for i, group in enumerate(available):
            view = _series_history_view(group, start_date, end_date)
            with cols_idx[i % 3]:
                _plot_with_ma(view, group, "지수", group, key=f"stock_index_{group}")

    with tab_yieldgap:
        data = _yield_gap_data(start_date, end_date)

        col1, col2 = st.columns(2)
        with col1:
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=data["날짜"], y=data["1/PER"], name="1/PER",
                                       line=dict(color="black", width=2)))
            fig1.add_trace(go.Scatter(x=data["날짜"], y=data["국고채 3년"], name="국고채 3년",
                                       line=dict(color="#2980B9", width=2)))
            fig1.update_layout(title="1/PER vs 국고채 3년", yaxis_title="%", height=420,
                                legend=dict(orientation="h", y=-0.2), margin=dict(t=40))
            st.plotly_chart(fig1, use_container_width=True, key="stock_1overper")
        with col2:
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=data["날짜"], y=data["갭"], name="Yield Gap",
                                       line=dict(color="black", width=2)))
            fig2.add_hline(y=3, line_color="blue", line_dash="dash",
                            annotation_text="적극매도", annotation_position="right")
            fig2.add_hline(y=6, line_color="#D4AC0D", line_dash="dash",
                            annotation_text="매수", annotation_position="right")
            fig2.add_hline(y=8, line_color="red", line_dash="dash",
                            annotation_text="적극매수", annotation_position="right")
            fig2.update_layout(title="Yield Gap (1/PER - 국고채 3년)", yaxis_title="%p", height=420, margin=dict(t=40))
            st.plotly_chart(fig2, use_container_width=True, key="stock_yieldgap")


# ================================================================ 신용스프레드
def page_credit():
    st.title("🏦 신용스프레드")

    col_a, col_b = st.columns(2)
    with col_a:
        base_group2 = st.segmented_control("기준 커브", ["국고채", "통안채"], default="국고채", key="credit_base")
        base_group2 = base_group2 or "국고채"
    with col_b:
        credit_options = [g for g in CREDIT_GROUPS_ORDER if g not in ("국고채", "통안채")]
        credit_group = st.selectbox("비교할 신용채권", credit_options, key="credit_group")

    spread_df = credit_spread(df, credit_group, base_group2)
    latest_spread_date = spread_df["날짜"].max()
    latest_spread = spread_df[spread_df["날짜"] == latest_spread_date]

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader(f"{credit_group} vs {base_group2} 금리커브 ({latest_spread_date:%Y-%m-%d})")
        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(x=latest_spread["만기"], y=latest_spread["기준금리"],
                                   mode="lines+markers", name=base_group2))
        fig3.add_trace(go.Scatter(x=latest_spread["만기"], y=latest_spread["그룹금리"],
                                   mode="lines+markers", name=credit_group))
        fig3.update_layout(xaxis_title="만기", yaxis_title="금리 (%)", height=380,
                            legend=dict(orientation="h", y=-0.2), margin=dict(t=30))
        st.plotly_chart(fig3, use_container_width=True)

        st.subheader(f"신용스프레드 ({credit_group} − {base_group2}, bp)")
        fig4 = px.bar(latest_spread, x="만기", y="스프레드_bp")
        fig4.update_layout(height=320, yaxis_title="bp", margin=dict(t=10))
        st.plotly_chart(fig4, use_container_width=True)

    with col2:
        st.subheader("스프레드 히스토리")
        available_tenors = [t for t in CURVE_TENORS if t in spread_df["만기"].unique()]
        spread_tenor = st.selectbox("만기 선택", available_tenors,
                                     index=available_tenors.index("3Y") if "3Y" in available_tenors else 0,
                                     key="credit_tenor")
        hist_spread = spread_df[spread_df["만기"] == spread_tenor].sort_values("날짜")
        fig5 = px.line(hist_spread, x="날짜", y="스프레드_bp")
        fig5.update_layout(height=280, yaxis_title="bp", margin=dict(t=10))
        st.plotly_chart(fig5, use_container_width=True)

        st.subheader("등급 사다리 (선택 만기, bp)")
        ladder_tenor = st.selectbox("만기 선택 ", available_tenors,
                                     index=available_tenors.index("3Y") if "3Y" in available_tenors else 0,
                                     key="ladder_tenor")
        ladder_rows = []
        for g in credit_options:
            gs = credit_spread(df, g, base_group2)
            gs_latest = gs[(gs["만기"] == ladder_tenor) & (gs["날짜"] == gs["날짜"].max())]
            if not gs_latest.empty:
                ladder_rows.append({"그룹": g, "스프레드_bp": gs_latest["스프레드_bp"].iloc[0]})
        ladder_df = pd.DataFrame(ladder_rows)
        fig6 = px.bar(ladder_df, x="스프레드_bp", y="그룹", orientation="h")
        fig6.update_layout(height=430, margin=dict(t=10), yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig6, use_container_width=True)

    st.subheader("스프레드 데이터 테이블 (최근 스냅샷)")
    st.dataframe(
        latest_spread[["만기", "기준금리", "그룹금리", "스프레드_bp"]]
        .rename(columns={"기준금리": f"{base_group2}(%)", "그룹금리": f"{credit_group}(%)"})
        .reset_index(drop=True),
        use_container_width=True,
    )


# ================================================================ IRS
def page_irs():
    st.title("🔁 IRS 커브 / 본드스왑 스프레드")

    latest_irs = latest_curve(df, "IRS")
    latest_irs_date = latest_irs["날짜"].max()
    irs_spread_df = credit_spread(df, "IRS", "국고채")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader(f"IRS 금리커브 ({latest_irs_date:%Y-%m-%d})")
        fig7 = go.Figure()
        fig7.add_trace(go.Scatter(x=latest_irs["만기"], y=latest_irs["값"], mode="lines+markers", name="IRS"))
        latest_govt = latest_curve(df, "국고채")
        fig7.add_trace(go.Scatter(x=latest_govt["만기"], y=latest_govt["값"], mode="lines+markers", name="국고채"))
        fig7.update_layout(xaxis_title="만기", yaxis_title="금리 (%)", height=380,
                            legend=dict(orientation="h", y=-0.2), margin=dict(t=30))
        st.plotly_chart(fig7, use_container_width=True)

        st.subheader("본드스왑 스프레드 (IRS − 국고채, bp, 공통 만기)")
        latest_irs_spread = irs_spread_df[irs_spread_df["날짜"] == irs_spread_df["날짜"].max()]
        fig8 = px.bar(latest_irs_spread, x="만기", y="스프레드_bp")
        fig8.update_layout(height=320, yaxis_title="bp", margin=dict(t=10))
        st.plotly_chart(fig8, use_container_width=True)

    with col2:
        st.subheader("IRS 만기별 히스토리")
        irs_tenor = st.selectbox("만기 선택", IRS_TENORS,
                                  index=IRS_TENORS.index("3Y") if "3Y" in IRS_TENORS else 0, key="irs_hist_tenor")
        irs_hist = curve_history(df, "IRS", irs_tenor)
        fig9 = px.line(irs_hist, x="날짜", y="값")
        fig9.update_layout(height=280, yaxis_title="금리 (%)", margin=dict(t=10))
        st.plotly_chart(fig9, use_container_width=True)

        st.subheader("본드스왑 스프레드 히스토리")
        common_tenors = [t for t in CURVE_TENORS if t in irs_spread_df["만기"].unique()]
        bs_tenor = st.selectbox("만기 선택", common_tenors,
                                 index=common_tenors.index("3Y") if "3Y" in common_tenors else 0, key="bs_tenor")
        bs_hist = irs_spread_df[irs_spread_df["만기"] == bs_tenor].sort_values("날짜")
        fig10 = px.line(bs_hist, x="날짜", y="스프레드_bp")
        fig10.add_hline(y=0, line_dash="dot", line_color="gray")
        fig10.update_layout(height=430, yaxis_title="bp", margin=dict(t=10))
        st.plotly_chart(fig10, use_container_width=True)

    st.subheader("IRS 데이터 테이블 (최근 스냅샷)")
    st.dataframe(latest_irs[["만기", "값"]].rename(columns={"값": "IRS(%)"}).reset_index(drop=True),
                 use_container_width=True)


# ================================================================ 단기금리
def page_short():
    st.title("📉 단기금리")

    st.subheader("기준금리 / CD(91일) 히스토리")
    base_rate = df[df["그룹"] == "기준금리"][["날짜", "값"]].sort_values("날짜").rename(columns={"값": "기준금리"})
    cd91 = df[df["그룹"] == "CD"][["날짜", "값"]].sort_values("날짜").rename(columns={"값": "CD(91일)"})

    fig11 = go.Figure()
    fig11.add_trace(go.Scatter(x=base_rate["날짜"], y=base_rate["기준금리"], mode="lines", name="기준금리",
                                line=dict(shape="hv")))
    fig11.add_trace(go.Scatter(x=cd91["날짜"], y=cd91["CD(91일)"], mode="lines", name="CD(91일)"))
    fig11.update_layout(xaxis_title="날짜", yaxis_title="금리 (%)", height=480,
                         legend=dict(orientation="h", y=-0.2), margin=dict(t=30))
    st.plotly_chart(fig11, use_container_width=True)

    st.subheader("데이터 테이블 (최근 20영업일)")
    merged = pd.merge(base_rate, cd91, on="날짜", how="outer").sort_values("날짜", ascending=False)
    st.dataframe(merged.head(20).reset_index(drop=True), use_container_width=True)


ASSETS_DIR = Path(__file__).parent / "assets"


def _futures_richness_bp(futures_group: str) -> pd.DataFrame:
    """국채선물 가격 저평가(포인트)를 수익률(bp)로 환산: (저평가 * 100) / 수정듀레이션."""
    cheap = curve_history(df, futures_group, "저평가")[["날짜", "값"]].rename(columns={"값": "cheap"})
    dur = curve_history(df, futures_group, "수정듀레이션")[["날짜", "값"]].rename(columns={"값": "dur"})
    merged = cheap.merge(dur, on="날짜", how="inner").sort_values("날짜")
    merged["값"] = (merged["cheap"] * 100) / merged["dur"]
    return merged[["날짜", "값"]]


def _irs_vs_futures_yield_view(irs_tenor: str, futures_group: str, start_date, end_date) -> pd.DataFrame:
    a = curve_history(df, "IRS", irs_tenor)[["날짜", "값"]].rename(columns={"값": "irs"})
    b = curve_history(df, futures_group, "내재수익률")[["날짜", "값"]].rename(columns={"값": "fut"})
    merged = a.merge(b, on="날짜", how="inner").sort_values("날짜")
    merged["값"] = (merged["irs"] - merged["fut"]) * 100
    merged = merged.assign(**_with_ma(merged["값"]))
    return merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]


def _bss_vs_futures_scatter(start_date, end_date):
    bss = credit_spread(df, "IRS", "국고채")
    bss3 = bss[bss["만기"] == "3Y"][["날짜", "스프레드_bp"]].rename(columns={"스프레드_bp": "x"})
    fut3 = _futures_richness_bp("선물3년").rename(columns={"값": "y"})
    merged = bss3.merge(fut3, on="날짜", how="inner").sort_values("날짜")

    view = merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]

    fig = go.Figure()
    if len(view) >= 2:
        coeffs = np.polyfit(view["x"], view["y"], 1)
        xs = np.linspace(view["x"].min(), view["x"].max(), 50)
        ys = coeffs[0] * xs + coeffs[1]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color="#2980B9", width=2),
                                  name="회귀선", hoverinfo="skip"))

    hist = view.iloc[:-1] if len(view) > 0 else view
    fig.add_trace(go.Scatter(x=hist["x"], y=hist["y"], mode="markers",
                              marker=dict(color="gray", size=8, opacity=0.35, line=dict(width=0)),
                              name="일별", hovertext=hist["날짜"].dt.strftime("%Y-%m-%d")))

    if len(view) > 0:
        latest = view.iloc[-1]
        fig.add_trace(go.Scatter(x=[latest["x"]], y=[latest["y"]], mode="markers",
                                  marker=dict(color="red", size=14, symbol="triangle-up",
                                              line=dict(width=1, color="black")),
                                  name=f"현재 ({latest['날짜']:%Y-%m-%d})"))

    fig.update_layout(title="BSS와 선물 저평", xaxis_title="IRS - KTB 3년 (bp)",
                       yaxis_title="3년 선물 저평(bp)",
                       height=480, showlegend=False, margin=dict(t=40))
    return fig


def _futures_implied_vs_irs_and_richness(start_date, end_date):
    """선물내재수익률(3Y) - IRS(3Y), 그리고 3년선물 저평을 한 차트에 (저평은 우측 반전축)."""
    implied_vs_irs = _irs_vs_futures_yield_view("3Y", "선물3년", start_date, end_date).copy()
    implied_vs_irs["값"] = -implied_vs_irs["값"]  # IRS-선물내재수익률의 부호를 뒤집어 선물내재수익률-IRS로
    richness = _futures_richness_bp("선물3년")
    richness = richness[(richness["날짜"].dt.date >= start_date) & (richness["날짜"].dt.date <= end_date)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=implied_vs_irs["날짜"], y=implied_vs_irs["값"], name="선물내재수익률-IRS 3년",
                              line=dict(color="#C0392B", width=2)))
    fig.add_trace(go.Scatter(x=richness["날짜"], y=richness["값"], name="3년선물 저평 (우)",
                              line=dict(color="#AAB7C4", width=1.6), yaxis="y2"))
    fig.add_hline(y=0, line_color="gray", line_width=1)
    fig.update_layout(
        title="선물내재수익률-IRS 3년 vs 3년선물 저평", height=420,
        yaxis=dict(title="(bp)"),
        yaxis2=dict(title="(bp)", overlaying="y", side="right", autorange="reversed"),
        legend=dict(orientation="h", y=-0.2), margin=dict(t=40),
    )
    return fig


IRS_KTB_SPREAD_TENORS = ["1Y", "2Y", "3Y", "5Y", "10Y", "30Y"]
IRS_KTB_ALL_TENORS = ["1Y", "2Y", "3Y", "4Y", "5Y", "10Y", "20Y", "30Y"]
IRS_FUTURES_PAIRS = [("3Y", "선물3년"), ("10Y", "선물10년")]


def _yeojeonchae_bss_2y_view(start_date, end_date) -> pd.DataFrame:
    """
    여전채 AA- BSS 2Y = (여전채AA-2Y + (여전채AA-2Y - 여전채AA-1Y)) + (CD - IRS2Y) - (IRS2Y - IRS1Y), bp 환산(x100)
    """
    a = curve_history(df, "기타금융채AA-", "2Y")[["날짜", "값"]].rename(columns={"값": "yjc2"})
    b = curve_history(df, "기타금융채AA-", "1Y")[["날짜", "값"]].rename(columns={"값": "yjc1"})
    c = df[df["그룹"] == "CD"][["날짜", "값"]].rename(columns={"값": "cd"})
    d = curve_history(df, "IRS", "2Y")[["날짜", "값"]].rename(columns={"값": "irs2"})
    e = curve_history(df, "IRS", "1Y")[["날짜", "값"]].rename(columns={"값": "irs1"})
    m = a.merge(b, on="날짜", how="inner").merge(c, on="날짜", how="inner") \
        .merge(d, on="날짜", how="inner").merge(e, on="날짜", how="inner").sort_values("날짜")
    m["값"] = ((m["yjc2"] + (m["yjc2"] - m["yjc1"])) + (m["cd"] - m["irs2"]) - (m["irs2"] - m["irs1"])) * 100
    return m[(m["날짜"].dt.date >= start_date) & (m["날짜"].dt.date <= end_date)][["날짜", "값"]]


def _abcp_view(start_date, end_date) -> pd.DataFrame:
    hist = df[df["그룹"] == "ABCP A1 3개월"][["날짜", "값"]].sort_values("날짜")
    return hist[(hist["날짜"].dt.date >= start_date) & (hist["날짜"].dt.date <= end_date)]


def _bss_abcp_dual_chart(start_date, end_date):
    bss = _yeojeonchae_bss_2y_view(start_date, end_date).rename(columns={"값": "bss"})
    abcp = _abcp_view(start_date, end_date).rename(columns={"값": "abcp"})
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=bss["날짜"], y=bss["bss"], name="여전채AA- BSS 2Y",
                              line=dict(color="#C0392B", width=2)))
    fig.add_trace(go.Scatter(x=abcp["날짜"], y=abcp["abcp"], name="ABCP A1 3개월 (우)",
                              line=dict(color="#2980B9", width=1.6), yaxis="y2"))
    fig.update_layout(
        title="여전채AA- BSS 2Y vs ABCP A1 3개월", height=400,
        yaxis=dict(title="bp"), yaxis2=dict(title="%", overlaying="y", side="right"),
        legend=dict(orientation="h", y=-0.2), margin=dict(t=40),
    )
    return fig


def _bss_minus_abcp_view(start_date, end_date) -> pd.DataFrame:
    """BSS(bp) - ABCP(%를 bp로 환산 = x100). 단위를 bp로 맞추기 위해 ABCP도 x100 처리."""
    bss = _yeojeonchae_bss_2y_view(start_date, end_date).rename(columns={"값": "bss"})
    abcp = _abcp_view(start_date, end_date).rename(columns={"값": "abcp"})
    m = bss.merge(abcp, on="날짜", how="inner").sort_values("날짜")
    m["값"] = m["bss"] - m["abcp"] * 100
    return m[["날짜", "값"]]


def _irs_ktb_vs_futures_dual_axis(start_date, end_date):
    ktb3 = _cross_group_spread_view("IRS", "국고채", "3Y", start_date, end_date)
    fut3 = _futures_richness_bp("선물3년")
    fut3 = fut3[(fut3["날짜"].dt.date >= start_date) & (fut3["날짜"].dt.date <= end_date)]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=ktb3["날짜"], y=ktb3["값"], name="IRS-KTB 3Y",
                              line=dict(color="#C0392B", width=2)))
    fig.add_trace(go.Scatter(x=fut3["날짜"], y=fut3["값"], name="3년선물 저평 (우)",
                              line=dict(color="#AAB7C4", width=1.6), yaxis="y2"))
    fig.add_hline(y=0, line_color="gray", line_width=1)
    fig.update_layout(
        title="IRS-KTB 3Y vs 3년 선물 저평", height=420,
        yaxis=dict(title="(bp)"),
        yaxis2=dict(title="(bp)", overlaying="y", side="right", autorange="reversed"),
        legend=dict(orientation="h", y=-0.2), margin=dict(t=40),
    )
    return fig


# ================================================================ Relative Value
def page_relative_value():
    st.title("⚖️ Relative Value")

    tab_valuation, tab_irsktb, tab_irsfut = st.tabs(["Valuation", "IRS-KTB", "IRS-선물"])

    irs_dates = df.loc[df["그룹"] == "IRS", "날짜"]
    min_date, max_date = irs_dates.min().date(), irs_dates.max().date()

    with tab_valuation:
        start_date, end_date = period_selector(min_date, max_date, key_prefix="rv", default="1Y")

        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(_bss_vs_futures_scatter(start_date, end_date), use_container_width=True, key="rv_scatter")
        with col2:
            st.image(str(ASSETS_DIR / "trilemma_diagram.png"), use_container_width=True)

        _chart_gap()
        st.plotly_chart(_futures_implied_vs_irs_and_richness(start_date, end_date), use_container_width=True,
                         key="rv_implied_vs_richness")

        _chart_gap()
        st.plotly_chart(_irs_ktb_vs_futures_dual_axis(start_date, end_date), use_container_width=True,
                         key="rv_ktb_vs_fut")

        _chart_gap()
        col3, col4 = st.columns(2)
        with col3:
            st.plotly_chart(_bss_abcp_dual_chart(start_date, end_date), use_container_width=True, key="rv_bss_abcp")
        with col4:
            full_diff = _bss_minus_abcp_view(min_date, max_date)
            avg = full_diff["값"].mean()
            diff_view = _bss_minus_abcp_view(start_date, end_date)
            diff_view = diff_view.assign(**_with_ma(diff_view["값"]))
            _plot_with_ma(diff_view, "여전채AA- BSS 2Y - ABCP A1 3개월", "bp",
                          "BSS-ABCP", key="rv_bss_minus_abcp", avg_line=avg)

    with tab_irsfut:
        start_date3, end_date3 = period_selector(min_date, max_date, key_prefix="rv_irsfut", default="1Y")
        st.markdown("#### IRS-선물내재수익률")
        cols3 = st.columns(2)
        for i, (irs_tenor, futures_group) in enumerate(IRS_FUTURES_PAIRS):
            full_history = _irs_vs_futures_yield_view(irs_tenor, futures_group, min_date, max_date)
            avg = full_history["값"].mean()
            view = _irs_vs_futures_yield_view(irs_tenor, futures_group, start_date3, end_date3)
            with cols3[i]:
                _plot_with_ma(view, f"IRS-선물내재수익률 {irs_tenor}", "bp", f"IRS-선물 {irs_tenor}",
                              key=f"rv_irsfut_{irs_tenor}", avg_line=avg)

    with tab_irsktb:
        start_date2, end_date2 = period_selector(min_date, max_date, key_prefix="rv_irsktb", default="1Y")

        cols = st.columns(3)
        for i, tenor in enumerate(IRS_KTB_SPREAD_TENORS):
            full_history = _cross_group_spread_view("IRS", "국고채", tenor, min_date, max_date)
            avg = full_history["값"].mean()
            view = _cross_group_spread_view("IRS", "국고채", tenor, start_date2, end_date2)
            with cols[i % 3]:
                _plot_with_ma(view, f"IRS-KTB {tenor}", "bp", f"IRS-KTB {tenor}", key=f"rv_irsktb_{tenor}",
                              avg_line=avg)

        _chart_gap()
        st.markdown("#### 만기 추가")
        extra_tenors = st.multiselect("추가로 볼 만기 선택", IRS_KTB_ALL_TENORS, default=[], key="rv_irsktb_extra")
        if extra_tenors:
            cols_extra = st.columns(3)
            for i, tenor in enumerate(extra_tenors):
                full_history = _cross_group_spread_view("IRS", "국고채", tenor, min_date, max_date)
                avg = full_history["값"].mean()
                view = _cross_group_spread_view("IRS", "국고채", tenor, start_date2, end_date2)
                with cols_extra[i % 3]:
                    _plot_with_ma(view, f"IRS-KTB {tenor}", "bp", f"IRS-KTB {tenor}",
                                  key=f"rv_irsktb_extra_{tenor}", avg_line=avg)


# ================================================================ 세로 사이드바 내비게이션
nav = st.navigation([
    st.Page(page_domestic_rate, title="국내금리", icon="🏛️", default=True),
    st.Page(page_irs_detail, title="IRS", icon="🔁"),
    st.Page(page_relative_value, title="Relative Value", icon="⚖️"),
    st.Page(page_foreign_rate, title="해외금리", icon="🌍"),
    st.Page(page_fx, title="FX", icon="💱"),
    st.Page(page_commodity, title="원자재", icon="🛢️"),
    st.Page(page_stock, title="주식", icon="📈"),
    st.Page(page_credit, title="신용스프레드", icon="🏦"),
    st.Page(page_irs, title="IRS 커브 / 본드스왑 스프레드", icon="🔁"),
    st.Page(page_short, title="단기금리", icon="📉"),
], position="sidebar")
nav.run()
