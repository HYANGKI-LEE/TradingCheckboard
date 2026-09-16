import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from data_loader import (
    CREDIT_GROUPS_ORDER,
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


def _plot_with_ma(view: pd.DataFrame, title: str, yaxis_title: str, name: str, key: str):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=view["날짜"], y=view["값"], mode="lines", name=name, line=dict(width=2)))
    for w in MA_WINDOWS:
        fig.add_trace(go.Scatter(x=view["날짜"], y=view[f"MA{w}"], mode="lines",
                                  name=f"MA{w}", line=dict(width=1, dash="dot")))
    fig.update_layout(title=title, height=360, yaxis_title=yaxis_title,
                       legend=dict(orientation="h", y=-0.25), margin=dict(t=40))
    st.plotly_chart(fig, use_container_width=True, key=key)


# ================================================================ 국내금리
def page_domestic_rate():
    st.title("🏛️ 국내금리")

    govt_dates = df.loc[df["그룹"] == "국고채", "날짜"]
    min_date, max_date = govt_dates.min().date(), govt_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="domestic", default="5Y")

    tab_rates, tab_spread = st.tabs(["Rates", "스프레드"])

    with tab_rates:
        cols = st.columns(2)
        for i, tenor in enumerate(DOMESTIC_RATE_TENORS):
            hist = curve_history(df, "국고채", tenor).copy()
            hist = hist.assign(**_with_ma(hist["값"]))
            view = hist[(hist["날짜"].dt.date >= start_date) & (hist["날짜"].dt.date <= end_date)]
            with cols[i % 2]:
                _plot_with_ma(view, f"국고채 {tenor}", "금리 (%)", f"국고채 {tenor}", key=f"rate_{tenor}")

    with tab_spread:
        cols2 = st.columns(2)
        for i, (long_t, short_t) in enumerate(DOMESTIC_SPREADS):
            a = curve_history(df, "국고채", long_t)[["날짜", "값"]].rename(columns={"값": "장기"})
            b = curve_history(df, "국고채", short_t)[["날짜", "값"]].rename(columns={"값": "단기"})
            merged = a.merge(b, on="날짜", how="inner").sort_values("날짜")
            merged["값"] = (merged["장기"] - merged["단기"]) * 100
            merged = merged.assign(**_with_ma(merged["값"]))
            view = merged[(merged["날짜"].dt.date >= start_date) & (merged["날짜"].dt.date <= end_date)]
            label = f"{long_t}-{short_t}"
            with cols2[i % 2]:
                _plot_with_ma(view, f"국고채 {label} 스프레드", "bp", label, key=f"spread_{label}")


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


# ================================================================ 세로 사이드바 내비게이션
nav = st.navigation([
    st.Page(page_domestic_rate, title="국내금리", icon="🏛️", default=True),
    st.Page(page_credit, title="신용스프레드", icon="🏦"),
    st.Page(page_irs, title="IRS 커브 / 본드스왑 스프레드", icon="🔁"),
    st.Page(page_short, title="단기금리", icon="📉"),
], position="sidebar")
nav.run()
