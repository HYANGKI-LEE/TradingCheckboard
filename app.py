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


# ================================================================ 국고채/통안채
def page_govt():
    st.title("🏛️ 국고채 / 통안채")

    base_group = st.segmented_control("커브 선택", ["국고채", "통안채"], default="국고채", key="govt_group")
    base_group = base_group or "국고채"
    latest = latest_curve(df, base_group)
    latest_date = latest["날짜"].max()

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader(f"{base_group} 금리커브 ({latest_date:%Y-%m-%d})")

        all_dates = sorted(df.loc[df["그룹"] == base_group, "날짜"].unique(), reverse=True)
        compare_dates = st.multiselect(
            "비교할 날짜 추가 (커브 겹쳐보기)", options=all_dates, default=[],
            format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"), key="govt_compare_dates",
        )

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=latest["만기"], y=latest["값"], mode="lines+markers",
                                  name=f"{latest_date:%Y-%m-%d}", line=dict(width=3)))
        for d in compare_dates:
            snap = df[(df["그룹"] == base_group) & (df["날짜"] == d)].sort_values("만기")
            fig.add_trace(go.Scatter(x=snap["만기"], y=snap["값"], mode="lines+markers",
                                      name=f"{pd.Timestamp(d):%Y-%m-%d}", line=dict(dash="dot")))
        fig.update_layout(xaxis_title="만기", yaxis_title="금리 (%)", height=480,
                           legend=dict(orientation="h", y=-0.2), margin=dict(t=30))
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("만기별 히스토리")
        tenor = st.selectbox("만기 선택", CURVE_TENORS, index=CURVE_TENORS.index("3Y"), key="govt_tenor")
        hist = curve_history(df, base_group, tenor)
        fig2 = px.line(hist, x="날짜", y="값")
        fig2.update_layout(height=430, yaxis_title="금리 (%)", margin=dict(t=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("금리 데이터 테이블 (최근 스냅샷)")
    st.dataframe(latest[["만기", "값"]].rename(columns={"값": "금리(%)"}).reset_index(drop=True),
                 use_container_width=True)


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
    st.Page(page_govt, title="국고채/통안채", icon="🏛️", default=True),
    st.Page(page_credit, title="신용스프레드", icon="🏦"),
    st.Page(page_irs, title="IRS 커브 / 본드스왑 스프레드", icon="🔁"),
    st.Page(page_short, title="단기금리", icon="📉"),
], position="sidebar")
nav.run()
