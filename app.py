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


def _plot_with_ma(view: pd.DataFrame, title: str, yaxis_title: str, name: str, key: str):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=view["날짜"], y=view["값"], mode="lines", name=name,
                              line=dict(width=3, color="black")))
    for w in MA_WINDOWS:
        fig.add_trace(go.Scatter(x=view["날짜"], y=view[f"MA{w}"], mode="lines",
                                  name=f"MA{w}", line=dict(width=1.5, color=MA_COLORS[w])))
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
            view = _tenor_history_view("국고채", tenor, start_date, end_date)
            with cols[i % 2]:
                _plot_with_ma(view, f"국고채 {tenor}", "금리 (%)", f"국고채 {tenor}", key=f"rate_{tenor}")

    with tab_spread:
        cols2 = st.columns(2)
        for i, (long_t, short_t) in enumerate(DOMESTIC_SPREADS):
            view = _tenor_spread_view("국고채", long_t, short_t, start_date, end_date)
            label = f"{long_t}-{short_t}"
            with cols2[i % 2]:
                _plot_with_ma(view, f"국고채 {label} 스프레드", "bp", label, key=f"spread_{label}")


IRS_DETAIL_SUBTABS = [("Par rate", "IRS"), ("Zero rate", "IRS_ZERO"), ("Fwd rate", "IRS_FWD3M")]


# ================================================================ IRS (Par/Zero/Fwd)
def page_irs_detail():
    st.title("🔁 IRS")

    irs_dates = df.loc[df["그룹"] == "IRS", "날짜"]
    min_date, max_date = irs_dates.min().date(), irs_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="irs_detail", default="5Y")

    tabs = st.tabs([label for label, _ in IRS_DETAIL_SUBTABS])
    for tab, (label, group) in zip(tabs, IRS_DETAIL_SUBTABS):
        with tab:
            available = set(df.loc[df["그룹"] == group, "만기"].dropna().astype(str).unique())
            tenors = [t for t in IRS_ZERO_FWD_TENOR_ORDER if t in available]
            cols = st.columns(3)
            for i, tenor in enumerate(tenors):
                view = _tenor_history_view(group, tenor, start_date, end_date)
                with cols[i % 3]:
                    _plot_with_ma(view, f"{label} {tenor}", "%", f"{label} {tenor}",
                                  key=f"irsdetail_{group}_{tenor}")
                if (i + 1) % 3 == 0:
                    _chart_gap()


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


# ================================================================ 해외금리
def page_foreign_rate():
    st.title("🌍 해외금리")

    foreign_dates = df.loc[df["그룹"].isin(FOREIGN_COUNTRIES_ORDER), "날짜"]
    min_date, max_date = foreign_dates.min().date(), foreign_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="foreign", default="5Y")

    tab_rates, tab_spread_period, tab_spread_country = st.tabs(["Rates", "스프레드(기간)", "스프레드(국가간)"])

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
    start_date, end_date = period_selector(min_date, max_date, key_prefix="fx", default="5Y")

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
    "금": "🥇", "은": "🥈", "구리": "🟠", "알루미늄": "⚙️",
}


COMMODITY_CATEGORIES = {
    "🔥 에너지": ["WTI", "브렌트", "두바이유", "천연가스", "에탄올"],
    "🥇 귀금속": ["팔라듐", "백금", "금", "은"],
    "🌾 음식": [
        "KC HRW 밀", "미니 옥수수", "미니 콩", "미니 소맥", "옥수수", "대두유", "대두박", "귀리",
        "쌀", "대두", "시카고 SRW 밀", "버터", "치즈", "3등급 우유", "4등급 우유", "비육우",
        "무지방 건조우유", "돈육", "생우", "코코아", "커피", "오렌지주스", "설탕",
    ],
    "📊 기타": ["블룸버그 상품 지수", "다우 존스 부동산", "30 DAY FEDERAL FUNDS", "면화", "미국달러지수", "구리", "알루미늄"],
}


# ================================================================ 원자재
def page_commodity():
    st.title("🛢️ 원자재")

    commodity_dates = df.loc[df["그룹"].isin(COMMODITY_ORDER), "날짜"]
    min_date, max_date = commodity_dates.min().date(), commodity_dates.max().date()
    start_date, end_date = period_selector(min_date, max_date, key_prefix="commodity", default="5Y")

    for category, groups in COMMODITY_CATEGORIES.items():
        available = [g for g in groups if not df.loc[df["그룹"] == g].empty]
        if not available:
            continue
        st.subheader(category)
        cols = st.columns(3)
        for i, group in enumerate(available):
            view = _series_history_view(group, start_date, end_date)
            emoji = COMMODITY_EMOJI.get(group, "")
            with cols[i % 3]:
                _plot_with_ma(view, f"{emoji} {group}", "가격", f"{emoji} {group}", key=f"commodity_{group}")
        _chart_gap()


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
    st.Page(page_irs_detail, title="IRS", icon="🔁"),
    st.Page(page_foreign_rate, title="해외금리", icon="🌍"),
    st.Page(page_fx, title="FX", icon="💱"),
    st.Page(page_commodity, title="원자재", icon="🛢️"),
    st.Page(page_credit, title="신용스프레드", icon="🏦"),
    st.Page(page_irs, title="IRS 커브 / 본드스왑 스프레드", icon="🔁"),
    st.Page(page_short, title="단기금리", icon="📉"),
], position="sidebar")
nav.run()
