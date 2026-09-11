import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from data_loader import (
    TENOR_ORDER,
    IRS_LIQUID_TENORS,
    load_curve_data,
    latest_curve,
    spread_series,
)

st.set_page_config(page_title="채권/IRS 트레이딩 대시보드", layout="wide")

df, is_sample = load_curve_data()

st.title("📈 채권/IRS 트레이딩 대시보드")

if is_sample:
    st.info(
        "지금 보이는 데이터는 **샘플(더미) 데이터**입니다. "
        "인포맥스에서 뽑은 실제 커브 데이터 파일(`data/curve_data.xlsx`)을 넣으면 자동으로 실데이터로 전환됩니다.",
        icon="ℹ️",
    )

tab_ktb, tab_irs = st.tabs(["🏛️ 국고채 금리커브", "🔁 IRS 커브 / 스프레드"])

# ---------------------------------------------------------------- 국고채 탭
with tab_ktb:
    latest = latest_curve(df)
    latest_date = latest["날짜"].max()

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader(f"국고채 금리커브 ({latest_date:%Y-%m-%d})")

        compare_dates = st.multiselect(
            "비교할 날짜 추가 (커브 겹쳐보기)",
            options=sorted(df["날짜"].unique(), reverse=True),
            default=[],
            format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"),
            key="ktb_compare_dates",
        )

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=latest["만기"], y=latest["국고채금리"],
            mode="lines+markers", name=f"{latest_date:%Y-%m-%d}", line=dict(width=3),
        ))
        for d in compare_dates:
            snap = df[df["날짜"] == d].sort_values("만기")
            fig.add_trace(go.Scatter(
                x=snap["만기"], y=snap["국고채금리"],
                mode="lines+markers", name=f"{pd.Timestamp(d):%Y-%m-%d}",
                line=dict(dash="dot"),
            ))
        fig.update_layout(
            xaxis_title="만기", yaxis_title="금리 (%)",
            height=480, legend=dict(orientation="h", y=-0.2),
            margin=dict(t=30),
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("만기별 히스토리")
        tenor = st.selectbox("만기 선택", TENOR_ORDER, index=TENOR_ORDER.index("3Y"), key="ktb_tenor")
        hist = df[df["만기"] == tenor].sort_values("날짜")
        fig2 = px.line(hist, x="날짜", y="국고채금리")
        fig2.update_layout(height=430, yaxis_title="금리 (%)", margin=dict(t=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("금리 데이터 테이블 (최근 스냅샷)")
    st.dataframe(
        latest[["만기", "국고채금리", "IRS금리"]].reset_index(drop=True),
        use_container_width=True,
    )

# ---------------------------------------------------------------- IRS 탭
with tab_irs:
    spread_df = spread_series(df)
    irs_df = df.dropna(subset=["IRS금리"])
    latest_irs = latest_curve(irs_df.dropna(subset=["IRS금리"]))
    latest_spread = spread_series(latest_irs)

    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader(f"IRS 금리커브 vs 국고채 ({latest_date:%Y-%m-%d})")
        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(
            x=latest_irs["만기"], y=latest_irs["국고채금리"],
            mode="lines+markers", name="국고채",
        ))
        fig3.add_trace(go.Scatter(
            x=latest_irs["만기"], y=latest_irs["IRS금리"],
            mode="lines+markers", name="IRS",
        ))
        fig3.update_layout(
            xaxis_title="만기", yaxis_title="금리 (%)",
            height=380, legend=dict(orientation="h", y=-0.2), margin=dict(t=30),
        )
        st.plotly_chart(fig3, use_container_width=True)

        st.subheader("본드스왑 스프레드 (IRS − 국고채, bp)")
        fig4 = px.bar(latest_spread, x="만기", y="스프레드_bp")
        fig4.update_layout(height=320, yaxis_title="bp", margin=dict(t=10))
        st.plotly_chart(fig4, use_container_width=True)

    with col2:
        st.subheader("스프레드 히스토리")
        spread_tenor = st.selectbox(
            "만기 선택", sorted(IRS_LIQUID_TENORS, key=TENOR_ORDER.index),
            index=sorted(IRS_LIQUID_TENORS, key=TENOR_ORDER.index).index("3Y"),
            key="irs_tenor",
        )
        hist_spread = spread_df[spread_df["만기"] == spread_tenor].sort_values("날짜")
        fig5 = px.line(hist_spread, x="날짜", y="스프레드_bp")
        fig5.add_hline(y=0, line_dash="dot", line_color="gray")
        fig5.update_layout(height=430, yaxis_title="bp", margin=dict(t=10))
        st.plotly_chart(fig5, use_container_width=True)

    st.subheader("스프레드 데이터 테이블 (최근 스냅샷)")
    st.dataframe(
        latest_spread[["만기", "국고채금리", "IRS금리", "스프레드_bp"]].reset_index(drop=True),
        use_container_width=True,
    )
