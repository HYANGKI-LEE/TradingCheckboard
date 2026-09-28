"""
Streamlit 대시보드(app.py)를 GitHub Pages용 정적 HTML(docs/index.html)로 내보낸다.

app.py의 차트/표 생성 함수는 대부분 st.* 호출과 이미 분리된 순수 함수라
(go.Figure나 HTML 문자열을 리턴) 그대로 재사용한다 - Streamlit을 최소한으로 모킹해서
app.py를 모듈로 import한 뒤, 그 안의 데이터(app.df)와 함수(app._xxx)를 직접 호출한다.

기간(1M/3M/.../MAX) 선택은 서버 재계산 대신, 전체 기간 데이터를 한 번에 그려두고
Plotly range selector 버튼으로 클라이언트에서 바로 확대/축소하는 방식으로 대체한다.
드롭다운으로 조합을 고르는 위젯(크레딧 종류/테너, IRS 커스텀 스프레드 등)은 기본값만
정적으로 보여준다 - 모든 조합을 다 만들면 범위가 너무 커져서 이번 범위에서는 제외.
"""
import sys
import json
import shutil
import base64
import struct
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

import streamlit as st  # noqa: E402


class _NoOpCache:
    def __call__(self, *a, **k):
        def deco(f):
            return f
        return deco


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _cols(spec, **k):
    n = spec if isinstance(spec, int) else len(spec)
    return [_NullCtx() for _ in range(n)]


def _tabs(labels, **k):
    return [_NullCtx() for _ in labels]


class _NavStub:
    def run(self):
        pass


st.cache_data = _NoOpCache()
st.set_page_config = lambda **k: None
st.markdown = lambda *a, **k: None
st.title = lambda *a, **k: None
st.warning = lambda *a, **k: None
st.caption = lambda *a, **k: None
st.info = lambda *a, **k: None
st.write = lambda *a, **k: None
st.image = lambda *a, **k: None
st.dataframe = lambda *a, **k: None
st.subheader = lambda *a, **k: None
st.container = lambda **k: _NullCtx()
st.columns = _cols
st.tabs = _tabs
st.segmented_control = lambda *a, **k: None
st.date_input = lambda *a, **k: None
st.selectbox = lambda label, options, index=0, **k: (options[index] if options else None)
st.multiselect = lambda *a, default=None, **k: (default or [])
st.plotly_chart = lambda *a, **k: None
st.navigation = lambda pages, **k: _NavStub()
st.Page = lambda fn, **k: fn
st.stop = lambda: (_ for _ in ()).throw(SystemExit)

import app  # noqa: E402  (모킹 이후에 import해야 함)

df = app.df
MIN_D = df["날짜"].min().date()
MAX_D = df["날짜"].max().date()
# 전체 히스토리(1990~)를 다 박으면 차트 하나가 수백만 포인트라 파일이 감당 안 되는 크기로
# 커짐 - 정적 사이트는 최근 5년치만 내장하고(대부분 페이지 버튼 범위 1M~5Y를 커버), 장기평균
# 같은 스칼라 계산에만 전체 히스토리(MIN_D)를 그대로 사용한다.
HIST_START_D = app._preset_to_start("5Y", MIN_D, MAX_D)


# ================================================================ 차트/HTML 공용 유틸
# 라이브 앱과 동일한 기간 프리셋. 페이지(또는 탭) 상단에 딱 하나만 두고 그 안의 모든
# 차트에 한꺼번에 적용한다(차트마다 따로 버튼을 붙이던 이전 방식은 라이브 앱과 달라서 폐기).
PERIOD_PRESETS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "10Y", "MTD", "QTD", "YTD", "MAX", "설정"]


def _finalize(fig):
    fig.update_layout(margin=dict(t=60))
    return fig


_BDATA_DTYPES = {"f8": "d", "f4": "f", "i4": "i", "i2": "h", "u4": "I", "u2": "H", "i1": "b", "u1": "B"}


def _decode_bdata(value):
    """y값을 pandas Series 그대로 넘기면 Plotly가 {'dtype':'f8','bdata':'<base64>'} 같은
    자체 압축 바이너리 포맷으로 인코딩해버리는 경우가 있다(우리 코드가 다 이 경로를 탐) -
    이 상태로 그대로 JSON에 내보내면 브라우저 쪽 JS가 평범한 배열로 못 읽어서(기간 버튼
    누를 때 y축 재계산이 깨짐), 여기서 직접 다시 순수 숫자 리스트로 풀어준다."""
    if not (isinstance(value, dict) and "bdata" in value and "dtype" in value):
        return value
    fmt = _BDATA_DTYPES.get(value["dtype"])
    if fmt is None:
        return value  # 모르는 타입이면 안전하게 원본 유지
    raw_bytes = base64.b64decode(value["bdata"])
    n = len(raw_bytes) // struct.calcsize(fmt)
    nums = struct.unpack(f"<{n}{fmt}", raw_bytes)
    return [None if v != v else v for v in nums]  # NaN -> None (v!=v는 NaN 판별)


_chart_counter = [0]


def chart_div(fig, timeseries: bool = True) -> str:
    """fig.to_html()은 날짜를 마이크로초 단위 ISO 문자열로 통째로 내장해서 차트 하나가
    수백 KB씩 나감 - 직접 JSON을 만들면서 날짜 축만 'YYYY-MM-DD'로 짧게 바꿔서 내장
    용량을 줄인다(카테고리 축(바 차트 등)은 변환 실패하면 원본 그대로 둠).
    timeseries: 날짜 x축 차트인지 여부 - period-scope의 공통 기간 버튼이 이 값이
    True인 차트에만 적용된다(히트맵/막대그래프/산점도는 건드리면 축이 깨짐)."""
    _finalize(fig)
    _chart_counter[0] += 1
    div_id = f"chart{_chart_counter[0]}"
    raw = fig.to_plotly_json()
    for trace in raw["data"]:
        x = trace.get("x")
        if x is not None and len(x) > 0:
            try:
                trace["x"] = [str(pd.Timestamp(v).date()) for v in x]
            except (ValueError, TypeError):
                pass
        if "y" in trace:
            trace["y"] = _decode_bdata(trace["y"])
    payload = json.dumps({"data": raw["data"], "layout": raw["layout"], "timeseries": timeseries},
                          separators=(",", ":"), default=str)
    ts_attr = "1" if timeseries else "0"
    return (f'<div id="{div_id}" class="plotly-chart" data-ts="{ts_attr}"></div>'
            f'<script type="application/json" id="{div_id}-data">{payload}</script>'
            f'<script>renderChart("{div_id}");</script>')


_scope_counter = [0]


def period_scope(default: str, body: str) -> str:
    """body: 이 스코프로 감쌀 HTML(그 안의 chart_div가 만든 차트들이 이 스코프의
    기간 버튼 적용 대상이 됨). 라이브 앱에서 기간선택 위젯 하나가 페이지 전체
    (또는 탭 하나)에 적용되던 것과 동일한 범위 단위."""
    _scope_counter[0] += 1
    scope_id = f"scope{_scope_counter[0]}"
    btns = "".join(
        f'<button data-preset="{p}" onclick="{"toggleCustom" if p == "설정" else "applyPeriod"}'
        f'(\'{scope_id}\'{"" if p == "설정" else f",\'{p}\'"},this)">{p}</button>'
        for p in PERIOD_PRESETS
    )
    bar = (f'<div class="period-bar" data-scope="{scope_id}">'
           f'<span class="period-label">기간</span>{btns}</div>'
           f'<div class="custom-range" id="{scope_id}-customBox">'
           f'<input type="date" id="{scope_id}-start"> ~ '
           f'<input type="date" id="{scope_id}-end"> '
           f'<button onclick="applyCustomRange(\'{scope_id}\')">적용</button></div>')
    return f'<div class="period-scope" data-scope="{scope_id}" data-default="{default}">{bar}{body}</div>'


def plot_with_ma_fig(view, title, yaxis_title, name, avg_line=None):
    """app._plot_with_ma와 동일한 로직이지만 st.plotly_chart 대신 fig를 리턴 (재사용 위해 복제)."""
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=view["날짜"], y=view["값"], mode="lines", name=name,
                              line=dict(width=3, color="black")))
    for w in app.MA_WINDOWS:
        fig.add_trace(go.Scatter(x=view["날짜"], y=view[f"MA{w}"], mode="lines",
                                  name=f"MA{w}", line=dict(width=1.5, color=app.MA_COLORS[w])))
    if avg_line is not None:
        fig.add_hline(y=avg_line, line_dash="dash", line_color="black", line_width=1.2,
                      annotation_text=f"장기평균 {avg_line:.1f}", annotation_position="top left",
                      annotation_font_size=10)
    fig.update_layout(title=title, height=360, yaxis_title=yaxis_title,
                       legend=dict(orientation="h", y=-0.25), margin=dict(t=40))
    return fig


def plot_change_multi_fig(series_list, title):
    """실시간 앱은 기간선택 위젯 기본값(YTD)을 쓰는데, 정적 버전은 그 기준일(연초) 고정."""
    import plotly.graph_objects as go
    ytd_start = app._preset_to_start("YTD", MIN_D, MAX_D)
    fig = go.Figure()
    for label, group, tenor in series_list:
        view = app._change_since_start_view(group, tenor, ytd_start, MAX_D)
        if view.empty:
            continue
        fig.add_trace(go.Scatter(x=view["날짜"], y=view["변동"], mode="lines", name=label, line=dict(width=2)))
    fig.add_hline(y=0, line_color="gray", line_width=1)
    fig.update_layout(title=title, yaxis_title="(bp)", height=460,
                       legend=dict(orientation="h", y=-0.22), margin=dict(t=40))
    return fig


def grid(n: int, cells: list[str]) -> str:
    cols = " ".join(["1fr"] * n)
    items = "".join(f'<div class="cell">{c}</div>' for c in cells)
    return f'<div class="grid" style="grid-template-columns:{cols};">{items}</div>'


def section(title: str, body: str) -> str:
    heading = f'<h4>{title}</h4>' if title else ""
    return f'<div class="section">{heading}{body}</div>'


_tab_counter = [0]


def tabs_html(labels: list[str], bodies: list[str]) -> str:
    _tab_counter[0] += 1
    gid = f"tabgroup{_tab_counter[0]}"
    btns = "".join(
        f'<button class="tab-btn{" active" if i == 0 else ""}" '
        f'onclick="showTab(\'{gid}\',{i})">{lbl}</button>'
        for i, lbl in enumerate(labels)
    )
    panels = "".join(
        f'<div class="tab-panel{" active" if i == 0 else ""}" id="{gid}-{i}">{body}</div>'
        for i, body in enumerate(bodies)
    )
    return f'<div class="tabgroup" data-group="{gid}"><div class="tab-bar">{btns}</div>{panels}</div>'


def df_to_html(d) -> str:
    cols = d.columns.tolist()
    html = ['<table style="width:100%;border-collapse:collapse;font-size:15px;">',
            '<tr style="border-bottom:2px solid #333;">']
    for c in cols:
        html.append(f'<th style="text-align:right;padding:4px 6px;">{c}</th>')
    html.append("</tr>")
    for _, row in d.iterrows():
        html.append('<tr style="border-bottom:1px solid #eee;">')
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                cell = f"{v:.1f}" if v == v else "-"
            else:
                cell = str(v)
            html.append(f'<td style="text-align:right;padding:3px 6px;">{cell}</td>')
        html.append("</tr>")
    html.append("</table>")
    return "".join(html)


NOTE_SIMPLIFIED = ('<p class="note">이 화면은 GitHub Pages 정적 버전이라 아래 항목만 라이브 앱과 다릅니다: '
                    '① 기간 버튼은 1M~5Y 범위만 지원(그 이전 히스토리는 미포함), '
                    '② 드롭다운으로 조합을 고르는 차트는 기본 선택값만 표시.</p>')


# ================================================================ Main
def render_main() -> str:
    rate_rows = [app._rate_change_row(label, group, tenor) for label, group, tenor in app.MAIN_RATE_ROWS]
    credit_rows = [app._rate_change_row(f"{label}(3Y)", f"크레딧_{suffix}", "3Y", scale=1)
                   for label, suffix in app.MAIN_CREDIT_ROWS]
    sections = [("금리", rate_rows), ("크레딧", credit_rows)]
    table1 = app._render_rate_table(sections, highlight=set(), change_label="변동(bp, Tick)")

    table2 = app._render_spread_matrix_table(app._main_spread_sections_cached(app.EXCEL_PATH.stat().st_mtime))

    body = (
        section("주요 금리 : 변동", table1) +
        section("주요 스프레드 : 변동",
                '<p class="caption">CD/CP는 통안 단기물 데이터가 없어 기준금리 대비로 대체했습니다. '
                'A1CP(6M)/A20CP(6M), 미국 IG/HY는 현재 데이터에 해당 시계열이 없어 제외했습니다.</p>' + table2)
    )
    return tabs_html(["변동"], [body])


# ================================================================ 국내금리
def render_domestic() -> str:
    change_sections = [
        ("국고채", [app._rate_change_row(f"국고 {t}", "국고채", t) for t in ["3Y", "5Y", "10Y"]]),
        ("선물(내재수익률,%)", [app._rate_change_row(f"선물 {t}", g, "내재수익률")
                            for t, g in [("3년", "선물3년"), ("10년", "선물10년")]]),
        ("IRS(%)", [app._rate_change_row(t, "IRS", t) for t in app.IRS_TABLE_TENORS]),
    ]
    table = app._render_rate_table(change_sections, highlight={"1Y", "2Y", "3Y"})
    bar_chart = chart_div(app._daily_change_bar_chart(), timeseries=False)
    trend_chart = chart_div(plot_change_multi_fig(app.DOMESTIC_CHANGE_SERIES, "주요금리 변동 추이"))
    tab_change = (section("주요 금리", grid(2, [table, bar_chart])) +
                  section("", period_scope("YTD", trend_chart)))

    rate_cells = []
    for tenor in app.DOMESTIC_RATE_TENORS:
        view = app._tenor_history_view("국고채", tenor, HIST_START_D, MAX_D)
        rate_cells.append(chart_div(plot_with_ma_fig(view, f"국고채 {tenor}", "금리 (%)", f"국고채 {tenor}")))
    tab_rates = grid(2, rate_cells)

    spread_cells = []
    for long_t, short_t in app.DOMESTIC_SPREADS:
        view = app._tenor_spread_view("국고채", long_t, short_t, HIST_START_D, MAX_D)
        label = f"{long_t}-{short_t}"
        spread_cells.append(chart_div(plot_with_ma_fig(view, f"국고채 {label} 스프레드", "bp", label)))
    tab_spread = grid(2, spread_cells)

    futures_sections = []
    for label, futures_group in [("3년", "선물3년"), ("5년", "선물5년"), ("10년", "선물10년"), ("30년", "선물30년")]:
        if df.loc[df["그룹"] == futures_group].empty:
            continue
        price_view = app._tenor_history_view(futures_group, "현재가", HIST_START_D, MAX_D)
        richness_view = app._futures_richness_bp(futures_group)
        richness_view = richness_view.assign(**app._with_ma(richness_view["값"]))
        richness_view = richness_view[(richness_view["날짜"].dt.date >= HIST_START_D) &
                                       (richness_view["날짜"].dt.date <= MAX_D)]
        cells = [
            chart_div(plot_with_ma_fig(price_view, f"{label}국채선물 가격", "가격", f"{label}국채선물")),
            chart_div(plot_with_ma_fig(richness_view, f"{label}국채선물 저평", "bp", f"{label}국채선물 저평")),
        ]
        futures_sections.append(section(f"{label}국채선물", grid(2, cells)))
    tab_futures = "".join(futures_sections)

    body = tabs_html(["변동", "Rates", "스프레드", "선물"], [tab_change, tab_rates, tab_spread, tab_futures])
    return period_scope("1Y", body)


# ================================================================ 크레딧
def render_credit() -> str:
    metric = "1d"
    table1 = app._render_credit_wide_table(metric, lambda g, t: app._rate_change_row("", g, t, scale=1),
                                            "현재값(bp)", 1, "변동(bp)")
    table2 = app._render_credit_wide_table(metric, app._credit_rate_row, "현재값(%)", 3, "변동(bp)")
    tab_change = (
        '<p class="caption">아래 표는 "변동 기준 1d" 고정입니다(정적 버전이라 기준 변경 불가).</p>' +
        section("크레딧 스프레드 : 테너별", table1) +
        section("크레딧 금리 : 테너별", table2)
    )

    spread_sections = []
    for section_label, data_prefix, grades in app.CREDIT_DETAIL_SECTIONS:
        available_grades = [(dg, ds) for dg, ds in grades
                             if not df.loc[df["그룹"] == f"크레딧_{data_prefix}{ds}"].empty]
        if not available_grades:
            continue
        cells = [chart_div(app._credit_spread_trend_chart(f"{section_label} {display_grade}", data_prefix,
                                                            data_suffix, HIST_START_D, MAX_D))
                 for display_grade, data_suffix in available_grades]
        spread_sections.append(section(section_label, grid(3, cells)))
    tab_spread = period_scope("1Y", "".join(spread_sections))

    heatmap = app._excess_return_heatmap_cached(app.EXCEL_PATH.stat().st_mtime)
    heatmap_html = chart_div(heatmap, timeseries=False) if heatmap is not None \
        else '<p class="caption">계산에 필요한 데이터가 부족합니다.</p>'
    excess_items = [
        (f"{section_label} {display_grade}", f"크레딧_{data_prefix}{data_suffix}")
        for section_label, data_prefix, grades in app.CREDIT_DETAIL_SECTIONS
        for display_grade, data_suffix in grades
    ]
    default_label, default_group = excess_items[0]
    default_tenor = app.EXCESS_RETURN_TENORS[4]  # 3Y, 라이브 앱 기본 선택과 동일
    series = app._excess_return_series_cached(default_group, default_tenor, app.EXCEL_PATH.stat().st_mtime)
    series = series[(series["날짜"].dt.date >= HIST_START_D) & (series["날짜"].dt.date <= MAX_D)]
    trend_html = "<p class='caption'>선택한 조합의 데이터가 없습니다.</p>"
    if not series.empty:
        view = series.assign(**app._with_ma(series["값"]))
        trend_html = chart_div(plot_with_ma_fig(view, f"{default_label} {default_tenor}Y 초과기대수익률 추이", "%p",
                                                 f"{default_label} {default_tenor}Y"))
    tab_excess = (
        '<p class="caption">기대수익률 = Roll-down에 따른 Capital gain + Coupon (보유 6개월 기준). '
        '초과 기대수익률 = 크레딧 기대수익률 - 국고채 기대수익률 (같은 만기끼리 비교)</p>' +
        heatmap_html +
        period_scope("1Y", section(f"초과 기대수익률 추이 (기본값: {default_label} / {default_tenor}Y)", trend_html))
    )

    tab_rate = '<p class="caption">추가 예정</p>'

    return tabs_html(["변동", "스프레드", "초과기대수익률", "금리"], [tab_change, tab_spread, tab_excess, tab_rate])


# ================================================================ 단기금리
def render_short() -> str:
    rows = [app._rate_change_row(label, group, tenor) for label, group, tenor in app.SHORT_RATE_ROWS]
    tab_change = app._render_rate_table([("단기금리", rows)], highlight=set())

    overlay = chart_div(app._short_curve_overlay_chart(HIST_START_D, MAX_D))
    pair_cells = []
    for label, group, tenor in app.SHORT_RATE_TREND_ITEMS:
        pair_cells.append(chart_div(app._short_rate_level_chart(label, group, tenor, HIST_START_D, MAX_D)))
        pair_cells.append(chart_div(app._short_rate_spread_chart(label, group, tenor, HIST_START_D, MAX_D)))
    tab_trend = period_scope("1Y", overlay + grid(2, pair_cells))

    return tabs_html(["변동", "추이"], [tab_change, tab_trend])


# ================================================================ IRS
def render_irs() -> str:
    available_tenors = set(df.loc[df["그룹"] == "IRS", "만기"].dropna().astype(str).unique())

    table1 = app._render_rate_table([("IRS(%)", [app._rate_change_row(t, "IRS", t) for t in app.IRS_TABLE_TENORS])],
                                     highlight={"1Y", "2Y", "3Y"})
    ktb_rows = [app._irs_ktb_stats(t) for t in app.IRS_TABLE_TENORS]
    table2 = app._render_irs_ktb_table(ktb_rows)
    matrix_rows = [app._irs_spread_matrix_row(long_t, short_t) for long_t, short_t in app.IRS_SPREAD_PAIRS
                   if long_t in available_tenors and short_t in available_tenors]
    table3 = app._render_irs_spread_matrix(matrix_rows)
    fwd_avail = set(df.loc[df["그룹"] == "IRS_FWD3M", "만기"].dropna().astype(str).unique())
    fwd_tenors = [t for t in app.IRS_ZERO_FWD_TENOR_ORDER if t in fwd_avail]
    fwd_rows = [app._irs_forward_row(t) for t in fwd_tenors]
    table4 = app._render_irs_forward_table(fwd_rows)
    tab_change = (
        section("IRS(%)", table1) + section("IRS-KTB", table2) + section("주요 IRS 스프레드", table3) +
        section("주요 IRS Forward Rate",
                '<p class="caption">금리인상 반영횟수 = (선도금리 - 기준금리) / 0.25%p, 참고용 근사치입니다.</p>' + table4)
    )

    def curve_tab(label, group):
        avail = set(df.loc[df["그룹"] == group, "만기"].dropna().astype(str).unique())
        tenors = [t for t in app.IRS_ZERO_FWD_TENOR_ORDER if t in avail]
        cells = [chart_div(plot_with_ma_fig(app._tenor_history_view(group, t, HIST_START_D, MAX_D),
                                             f"{label} {t}", "%", f"{label} {t}")) for t in tenors]
        return grid(3, cells)

    tab_par = curve_tab("Par rate", "IRS")
    tab_zero = curve_tab("Zero rate", "IRS_ZERO")
    tab_fwd_rate = curve_tab("Fwd rate", "IRS_FWD3M")

    import plotly.graph_objects as go
    tenor_a, tenor_b = "3Y", "1Y"
    base_rate = df[df["그룹"] == "기준금리"][["날짜", "값"]].sort_values("날짜")
    base_rate = base_rate[(base_rate["날짜"].dt.date >= HIST_START_D) & (base_rate["날짜"].dt.date <= MAX_D)]
    fig_base = go.Figure()
    custom_view = app._tenor_spread_view("IRS", tenor_a, tenor_b, HIST_START_D, MAX_D)
    fig_base.add_trace(go.Scatter(x=custom_view["날짜"], y=custom_view["값"], mode="lines",
                                   name=f"IRS {tenor_a}-{tenor_b}", line=dict(color="#2980B9", width=2.5)))
    fig_base.add_trace(go.Scatter(x=base_rate["날짜"], y=base_rate["값"], mode="lines", name="기준금리",
                                   line=dict(color="gray", shape="hv", width=2), yaxis="y2"))
    fig_base.update_layout(height=320, yaxis=dict(title="bp"),
                            yaxis2=dict(title="기준금리(%)", overlaying="y", side="right"),
                            legend=dict(orientation="h", y=-0.2), margin=dict(t=40))
    custom_chart = chart_div(fig_base)

    spread_cells = []
    for long_t, short_t in app.IRS_SPREAD_PAIRS:
        if long_t not in available_tenors or short_t not in available_tenors:
            continue
        view = app._tenor_spread_view("IRS", long_t, short_t, HIST_START_D, MAX_D)
        label = f"{short_t}-{long_t}"
        spread_cells.append(chart_div(plot_with_ma_fig(view, f"IRS {label}", "bp", label)))
    tab_spread = (section(f"기준금리 + 커스텀 스프레드 (기본값: {tenor_a}-{tenor_b})", custom_chart) +
                  grid(3, spread_cells))

    fly_cells = []
    for short_t, mid_t, long_t in app.IRS_BUTTERFLIES:
        if not all(t in available_tenors for t in (short_t, mid_t, long_t)):
            continue
        view = app._irs_butterfly_view(short_t, mid_t, long_t, HIST_START_D, MAX_D)
        label = f"{short_t}-{mid_t}-{long_t}"
        fly_cells.append(chart_div(plot_with_ma_fig(view, f"IRS 버터플라이 {label}", "bp", label)))
    tab_fly = grid(3, fly_cells)

    body = tabs_html(["변동", "Par rate", "스프레드", "Zero rate", "Fwd rate", "버터플라이"],
                      [tab_change, tab_par, tab_spread, tab_zero, tab_fwd_rate, tab_fly])
    return period_scope("1Y", body)


# ================================================================ Relative Value
def render_rv() -> str:
    scatter = chart_div(app._bss_vs_futures_scatter(HIST_START_D, MAX_D), timeseries=False)
    image_html = '<img src="assets/trilemma_diagram.png" style="width:100%;height:auto;">'
    implied_vs_cd = chart_div(app._futures_implied_vs_irs_and_cd(HIST_START_D, MAX_D))
    implied_vs_richness = chart_div(app._futures_implied_vs_irs_and_richness(HIST_START_D, MAX_D))
    ktb_vs_fut = chart_div(app._irs_ktb_vs_futures_dual_axis(HIST_START_D, MAX_D))
    bss_abcp = chart_div(app._bss_abcp_dual_chart(HIST_START_D, MAX_D))
    full_diff = app._bss_minus_abcp_view(MIN_D, MAX_D)
    avg = full_diff["값"].mean()
    diff_view = app._bss_minus_abcp_view(HIST_START_D, MAX_D)
    diff_view = diff_view.assign(**app._with_ma(diff_view["값"]))
    bss_minus_abcp = chart_div(plot_with_ma_fig(diff_view, "여전채AA- BSS 2Y - ABCP A1 3개월", "bp",
                                                 "BSS-ABCP", avg_line=avg))
    tab_valuation = period_scope("1Y",
        grid(2, [scatter, image_html]) + implied_vs_cd + implied_vs_richness + ktb_vs_fut +
        grid(2, [bss_abcp, bss_minus_abcp])
    )

    cells = []
    for irs_tenor, futures_group in app.IRS_FUTURES_PAIRS:
        full_history = app._irs_vs_futures_yield_view(irs_tenor, futures_group, MIN_D, MAX_D)
        avg = full_history["값"].mean()
        view = app._irs_vs_futures_yield_view(irs_tenor, futures_group, HIST_START_D, MAX_D)
        cells.append(chart_div(plot_with_ma_fig(view, f"IRS-선물내재수익률 {irs_tenor}", "bp",
                                                 f"IRS-선물 {irs_tenor}", avg_line=avg)))
    tab_irsfut = period_scope("1Y", section("IRS-선물내재수익률", grid(2, cells)))

    cells2 = []
    for tenor in app.IRS_KTB_SPREAD_TENORS:
        full_history = app._cross_group_spread_view("IRS", "국고채", tenor, MIN_D, MAX_D)
        avg = full_history["값"].mean()
        view = app._cross_group_spread_view("IRS", "국고채", tenor, HIST_START_D, MAX_D)
        cells2.append(chart_div(plot_with_ma_fig(view, f"IRS-KTB {tenor}", "bp", f"IRS-KTB {tenor}", avg_line=avg)))
    tab_irsktb = period_scope("1Y", grid(3, cells2))

    return tabs_html(["Valuation", "IRS-KTB", "IRS-선물"], [tab_valuation, tab_irsktb, tab_irsfut])


# ================================================================ 해외금리
def render_foreign() -> str:
    change_table = app._foreign_rate_change_table("10Y")
    table_html = df_to_html(change_table)
    bar_df = change_table[["국가", "전일대비(bp)"]].dropna().sort_values("전일대비(bp)", ascending=True)
    import plotly.graph_objects as go
    fig_bar = go.Figure(go.Bar(
        x=bar_df["전일대비(bp)"], y=bar_df["국가"], orientation="h",
        marker_color="#159895", text=bar_df["전일대비(bp)"], texttemplate="%{text:.1f}", textposition="outside",
    ))
    fig_bar.update_layout(title="전일대비(bp)", height=max(320, 28 * len(bar_df)), margin=dict(t=40, r=40))
    bar_html = chart_div(fig_bar, timeseries=False)
    tab_change = ('<p class="caption">만기: 10Y 기준 / 막대그래프 기준: 전일대비(bp) 고정</p>' +
                  grid(2, [table_html, bar_html]))

    rate_sections = []
    for country in app.FOREIGN_COUNTRIES_ORDER:
        available = set(df.loc[df["그룹"] == country, "만기"].dropna().astype(str).unique())
        target = app.FOREIGN_RATE_PREFERRED if "2Y" in available else app.FOREIGN_RATE_FALLBACK
        target = [t for t in target if t in available]
        if not target:
            continue
        cells = [chart_div(plot_with_ma_fig(app._tenor_history_view(country, t, HIST_START_D, MAX_D),
                                             f"{country} {t}", "금리 (%)", f"{country} {t}")) for t in target]
        rate_sections.append(section(f"{app._flag_html(country, 24)}{country}", grid(3, cells)))
    tab_rates = "".join(rate_sections)

    period_cells = []
    for country in app.FOREIGN_COUNTRIES_ORDER:
        available = set(df.loc[df["그룹"] == country, "만기"].dropna().astype(str).unique())
        short_t = "2Y" if "2Y" in available else ("3Y" if "3Y" in available else None)
        if short_t is None or "10Y" not in available:
            continue
        view = app._tenor_spread_view(country, "10Y", short_t, HIST_START_D, MAX_D)
        label = f"10Y-{short_t}"
        period_cells.append(chart_div(plot_with_ma_fig(view, f"{country} {label}", "bp", f"{country} {label}")))
    tab_spread_period = grid(3, period_cells)

    country_cells = []
    for a, b in app.CROSS_COUNTRY_SPREADS:
        view = app._cross_group_spread_view(app._foreign_group_key(a), app._foreign_group_key(b), "10Y",
                                             HIST_START_D, MAX_D)
        label = f"{a}-{b}"
        country_cells.append(chart_div(plot_with_ma_fig(view, f"{label} (10Y)", "bp", label)))
    tab_spread_country = grid(3, country_cells)

    body = tabs_html(["변동", "Rates", "스프레드(기간)", "스프레드(국가간)"],
                      [tab_change, tab_rates, tab_spread_period, tab_spread_country])
    return period_scope("1Y", body)


# ================================================================ FX
def render_fx() -> str:
    rows = [app._fx_change_row(g, is_cross, MIN_D, MAX_D) for g, is_cross in app.FX_ORDER]
    tab_change = app._render_rate_table([("FX", rows)], highlight=set(), price_label="현재가",
                                         price_decimals=3, change_label="변동(%)")

    cells = []
    for group, is_cross in app.FX_ORDER:
        view = app._fx_cross_krw_view(group, HIST_START_D, MAX_D) if is_cross \
            else app._series_history_view(group, HIST_START_D, MAX_D)
        flag = app.FX_FLAGS.get(group, "")
        cells.append(chart_div(plot_with_ma_fig(view, f"{flag} {group}", "환율", f"{flag} {group}")))
    tab_chart = period_scope("1Y", grid(3, cells))

    return tabs_html(["변동", "차트"], [tab_change, tab_chart])


# ================================================================ 원자재
def render_commodity() -> str:
    change_sections = []
    for category, groups in app.COMMODITY_CATEGORIES.items():
        available = [g for g in groups if g == "금은Ratio" or not df.loc[df["그룹"] == g].empty]
        if not available:
            continue
        rows = [app._commodity_change_row(g, MIN_D, MAX_D) for g in available]
        change_sections.append((category, rows))
    tab_change = app._render_rate_table(change_sections, highlight=set(), price_label="현재가",
                                         price_decimals=2, change_label="변동(%)")

    chart_sections = []
    for category, groups in app.COMMODITY_CATEGORIES.items():
        available = [g for g in groups if g == "금은Ratio" or not df.loc[df["그룹"] == g].empty]
        if not available:
            continue
        cells = []
        for group in available:
            emoji = app.COMMODITY_EMOJI.get(group, "")
            if group == "금은Ratio":
                view = app._commodity_ratio_view("금", "은", HIST_START_D, MAX_D)
                yaxis_title = "Ratio"
            else:
                view = app._series_history_view(group, HIST_START_D, MAX_D)
                yaxis_title = "가격"
            cells.append(chart_div(plot_with_ma_fig(view, f"{emoji} {group}", yaxis_title, f"{emoji} {group}")))
        chart_sections.append(section(category, grid(3, cells)))
    tab_chart = period_scope("1Y", "".join(chart_sections))

    return tabs_html(["변동", "차트"], [tab_change, tab_chart])


# ================================================================ 주식
def render_stock() -> str:
    import plotly.graph_objects as go
    available = [g for g in app.STOCK_INDEX_ORDER if not df.loc[df["그룹"] == g].empty]
    cells = [chart_div(plot_with_ma_fig(app._series_history_view(g, HIST_START_D, MAX_D), g, "지수", g))
             for g in available]
    tab_indices = grid(3, cells)

    data = app._yield_gap_data(HIST_START_D, MAX_D)
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(x=data["날짜"], y=data["1/PER"], name="1/PER", line=dict(color="black", width=2)))
    fig1.add_trace(go.Scatter(x=data["날짜"], y=data["국고채 3년"], name="국고채 3년", line=dict(color="#2980B9", width=2)))
    fig1.update_layout(title="1/PER vs 국고채 3년", yaxis_title="%", height=420,
                        legend=dict(orientation="h", y=-0.2), margin=dict(t=40))
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=data["날짜"], y=data["갭"], name="Yield Gap", line=dict(color="black", width=2)))
    fig2.add_hline(y=3, line_color="blue", line_dash="dash", annotation_text="적극매도",
                   annotation_position="right", annotation_font_color="blue", annotation_font_size=11)
    fig2.add_hline(y=6, line_color="#D4AC0D", line_dash="dash", annotation_text="매수",
                   annotation_position="right", annotation_font_color="#D4AC0D", annotation_font_size=11)
    fig2.add_hline(y=8, line_color="red", line_dash="dash", annotation_text="적극매수",
                   annotation_position="right", annotation_font_color="red", annotation_font_size=11)
    fig2.update_layout(title="Yield Gap (1/PER - 국고채 3년)", yaxis_title="%p", height=420, margin=dict(t=40, r=70))
    tab_yieldgap = grid(2, [chart_div(fig1), chart_div(fig2)])

    body = tabs_html(["Yield Gap", "주가추이"], [tab_yieldgap, tab_indices])
    return period_scope("5Y", body)


# ================================================================ 전체 조립
PAGES = [
    ("main", "✨", "Main", render_main),
    ("domestic", "🏛️", "국내금리", render_domestic),
    ("credit", "🏢", "크레딧", render_credit),
    ("short", "📉", "단기금리", render_short),
    ("irs", "🔁", "IRS", render_irs),
    ("rv", "⚖️", "Relative Value", render_rv),
    ("foreign", "🌍", "해외금리", render_foreign),
    ("fx", "💱", "FX", render_fx),
    ("commodity", "🛢️", "원자재", render_commodity),
    ("stock", "📈", "주식", render_stock),
]

CSS = """
:root { --accent:#2980B9; --border:#e5e7eb; --text:#222; --muted:#666; --bg:#fff; --side-bg:#f7f8fa; }
* { box-sizing: border-box; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
       color:var(--text); background:var(--bg); }
#layout { display:flex; min-height:100vh; }
#sidebar { width:220px; flex:0 0 220px; background:var(--side-bg); border-right:1px solid var(--border);
           padding:16px 8px; position:sticky; top:0; height:100vh; overflow-y:auto; }
#sidebar .brand { font-size:13px; color:var(--muted); padding:4px 12px 14px; }
#sidebar a { display:flex; align-items:center; gap:10px; padding:9px 12px; border-radius:8px; color:var(--text);
             text-decoration:none; font-size:15px; margin-bottom:2px; }
#sidebar a:hover { background:#eceff3; }
#sidebar a.active { background:#e3edf7; color:var(--accent); font-weight:600; }
#main { flex:1; min-width:0; padding:24px 32px 60px; }
#main h1 { font-size:28px; margin:0 0 6px; }
#top-note { color:var(--muted); font-size:13px; margin-bottom:18px; }
.page { display:none; }
.page.active { display:block; }
.tabgroup { margin-top:8px; }
.tab-bar { display:flex; gap:4px; border-bottom:2px solid var(--border); margin-bottom:16px;
           position:sticky; top:0; background:var(--bg); z-index:5; flex-wrap:wrap; }
.tab-btn { border:none; background:none; padding:10px 14px; font-size:14px; cursor:pointer; color:var(--muted);
           border-bottom:2px solid transparent; margin-bottom:-2px; }
.tab-btn:hover { color:var(--text); }
.tab-btn.active { color:var(--accent); border-bottom-color:var(--accent); font-weight:600; }
.tab-panel { display:none; }
.tab-panel.active { display:block; }
.section { margin:22px 0; }
.section h4 { font-size:16px; margin:0 0 8px; }
.grid { display:grid; gap:16px; margin:10px 0; }
.cell { min-width:0; }
.plotly-chart { width:100%; }
.caption, .note { color:var(--muted); font-size:13px; margin:4px 0 10px; }
table { font-size:14px; }
.period-bar { display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin:10px 0 16px;
              padding-bottom:12px; border-bottom:1px solid var(--border); }
.period-label { font-size:13px; color:var(--muted); margin-right:6px; }
.period-bar button { border:1px solid var(--border); background:#fff; border-radius:6px; padding:5px 11px;
                      font-size:13px; cursor:pointer; color:var(--text); }
.period-bar button:hover { background:#f2f4f7; }
.period-bar button.active { background:#fdecea; border-color:#e8a39b; color:#c0392b; font-weight:600; }
.custom-range { display:none; align-items:center; gap:6px; margin:-8px 0 16px; font-size:13px; }
.custom-range.show { display:flex; }
.custom-range input { border:1px solid var(--border); border-radius:6px; padding:4px 6px; font-size:13px; }
@media (max-width: 900px) {
  #sidebar { position:fixed; left:-240px; transition:left .2s; z-index:20; box-shadow:2px 0 8px rgba(0,0,0,.1); }
  #sidebar.open { left:0; }
  #main { padding:16px; }
  .grid { grid-template-columns:1fr !important; }
}
"""

JS = """
var CHART_MIN_DATE = new Date("__HIST_START__");
var CHART_MAX_DATE = new Date("__MAX_D__");

function pad2(n) { return (n < 10 ? '0' : '') + n; }
function isoDate(d) { return d.getFullYear() + '-' + pad2(d.getMonth()+1) + '-' + pad2(d.getDate()); }

function presetRange(preset) {
  var end = CHART_MAX_DATE, start;
  if (preset === 'MTD') start = new Date(end.getFullYear(), end.getMonth(), 1);
  else if (preset === 'QTD') { var q = Math.floor(end.getMonth()/3)*3; start = new Date(end.getFullYear(), q, 1); }
  else if (preset === 'YTD') start = new Date(end.getFullYear(), 0, 1);
  else if (preset === 'MAX') start = CHART_MIN_DATE;
  else {
    var n = parseInt(preset, 10), unit = preset.slice(-1);
    start = new Date(end.getTime());
    if (unit === 'M') start.setMonth(start.getMonth() - n); else start.setFullYear(start.getFullYear() - n);
  }
  if (start < CHART_MIN_DATE) start = CHART_MIN_DATE;
  return [start, end];
}

function rescaleY(gd, start, end) {
  // 기간 버튼으로 x축을 바꿔도 Plotly는 y축을 그대로 두는게 기본 동작이라(전체 데이터
  // 기준으로 고정) 확대할수록 위아래 여백만 늘어남 - 보이는 x범위 안의 값만으로
  // y축(+ 우측 y2축)을 직접 다시 계산해서 꽉 차게 맞춘다.
  // gd.data는 Plotly.js가 렌더링하면서 큰 숫자 배열을 자체 압축 포맷({dtype,bdata})으로
  // 바꿔치기해버려서 직접 못 읽음 - renderChart에서 따로 저장해둔 원본 배열(gd.__rawData)을 쓴다.
  if (!gd) return;
  var data = gd.__rawData || gd.data;
  if (!data) return;
  var t0 = start.getTime(), t1 = end.getTime();
  var ranges = {};
  data.forEach(function(trace) {
    if (!trace.x || !trace.y) return;
    var ax = (trace.yaxis === 'y2') ? 'yaxis2' : 'yaxis';
    if (!ranges[ax]) ranges[ax] = [Infinity, -Infinity];
    for (var i = 0; i < trace.x.length; i++) {
      var xv = new Date(trace.x[i]).getTime();
      if (xv >= t0 && xv <= t1) {
        var yv = trace.y[i];
        if (typeof yv === 'number' && !isNaN(yv)) {
          if (yv < ranges[ax][0]) ranges[ax][0] = yv;
          if (yv > ranges[ax][1]) ranges[ax][1] = yv;
        }
      }
    }
  });
  var upd = {'xaxis.range': [isoDate(start), isoDate(end)], 'xaxis.autorange': false};
  Object.keys(ranges).forEach(function(ax) {
    var mn = ranges[ax][0], mx = ranges[ax][1];
    if (mn === Infinity) return;
    var pad = (mx - mn) * 0.08;
    if (!pad) pad = (Math.abs(mx) || 1) * 0.08;
    upd[ax + '.range'] = [mn - pad, mx + pad];
    upd[ax + '.autorange'] = false;
  });
  Plotly.relayout(gd, upd);
}

function scopeCharts(scopeId) {
  // 스코프 안에 또 다른 period-scope가 중첩된 경우(페이지 공용 기간선택 + 탭 안의
  // 별도 기간선택이 같이 있는 경우), 중첩된 안쪽 스코프 소속 차트는 제외해야
  // 바깥 스코프 버튼을 눌렀을 때 안쪽 차트까지 같이 안 움직인다.
  var scope = document.querySelector('.period-scope[data-scope="' + scopeId + '"]');
  if (!scope) return [];
  return Array.prototype.slice.call(scope.querySelectorAll('.plotly-chart[data-ts="1"]'))
    .filter(function(el){ return el.closest('.period-scope') === scope; });
}

function applyPeriod(scopeId, preset, btn) {
  var r = presetRange(preset);
  scopeCharts(scopeId).forEach(function(el){ if (el.data) rescaleY(el, r[0], r[1]); });
  var bar = document.querySelector('.period-bar[data-scope="' + scopeId + '"]');
  bar.querySelectorAll('button').forEach(function(b){ b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  var box = document.getElementById(scopeId + '-customBox');
  if (box) box.classList.remove('show');
}

function toggleCustom(scopeId, btn) {
  var box = document.getElementById(scopeId + '-customBox');
  box.classList.toggle('show');
  var bar = document.querySelector('.period-bar[data-scope="' + scopeId + '"]');
  bar.querySelectorAll('button').forEach(function(b){ b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
}

function applyCustomRange(scopeId) {
  var s = document.getElementById(scopeId + '-start').value;
  var e = document.getElementById(scopeId + '-end').value;
  if (!s || !e) return;
  var start = new Date(s), end = new Date(e);
  scopeCharts(scopeId).forEach(function(el){ if (el.data) rescaleY(el, start, end); });
}

function initScopes() {
  document.querySelectorAll('.period-scope').forEach(function(scope) {
    var scopeId = scope.getAttribute('data-scope');
    var def = scope.getAttribute('data-default') || '1Y';
    var btn = scope.querySelector('.period-bar button[data-preset="' + def + '"]');
    applyPeriod(scopeId, def, btn);
  });
}

function resizeCharts(root) {
  root.querySelectorAll('.plotly-chart').forEach(function(el){
    if (window.Plotly && el.data) window.Plotly.Plots.resize(el);
  });
}
function showPage(id) {
  document.querySelectorAll('.page').forEach(function(el){ el.classList.remove('active'); });
  document.querySelectorAll('#sidebar a').forEach(function(el){ el.classList.remove('active'); });
  var page = document.getElementById('page-' + id);
  page.classList.add('active');
  document.getElementById('nav-' + id).classList.add('active');
  window.scrollTo(0, 0);
  var sb = document.getElementById('sidebar');
  if (sb.classList.contains('open')) sb.classList.remove('open');
  history.replaceState(null, '', '#' + id);
  // display:none 상태에서 Plotly.newPlot이 실행된 차트는 크기가 0으로 잡혀서
  // 페이지가 실제로 보이게 된 지금 다시 리사이즈 해줘야 함 (탭 전환도 showTab에서 동일 처리).
  setTimeout(function(){ resizeCharts(page.querySelector('.tab-panel.active') || page); }, 0);
}
function showTab(groupId, idx) {
  var group = document.querySelector('[data-group="' + groupId + '"]');
  var btns = group.querySelectorAll('.tab-btn');
  var panels = group.querySelectorAll('.tab-panel');
  btns.forEach(function(b, i){ b.classList.toggle('active', i === idx); });
  panels.forEach(function(p, i){ p.classList.toggle('active', i === idx); });
  setTimeout(function(){ resizeCharts(panels[idx]); }, 0);
}
function renderChart(id) {
  var text = document.getElementById(id + '-data').textContent;
  var gd = document.getElementById(id);
  // Plotly.newPlot에 넘기는 data는 Plotly.js가 내부적으로 값을 바꿔치기(bdata 압축)할 수
  // 있어서, rescaleY가 나중에 참조할 원본은 완전히 별개의 복사본으로 따로 파싱해둔다.
  gd.__rawData = JSON.parse(text).data;
  var payload = JSON.parse(text);
  Plotly.newPlot(gd, payload.data, payload.layout, {displaylogo:false, responsive:true});
}
document.addEventListener('DOMContentLoaded', function() {
  // 모든 period-scope의 기본 기간을 한 번에 적용(보이지 않는 탭/페이지 것도 포함 -
  // Plotly relayout은 숨겨진 요소에도 정상 반영되고, 실제로 보일 때 resize만 해주면 됨).
  setTimeout(initScopes, 50);
  var hash = location.hash.replace('#', '');
  var valid = document.getElementById('page-' + hash);
  showPage(valid ? hash : 'main');
});
"""


def build() -> str:
    nav_links = "".join(
        f'<a href="#{key}" id="nav-{key}" onclick="showPage(\'{key}\');return false;">'
        f'<span>{icon}</span><span>{label}</span></a>'
        for key, icon, label, _ in PAGES
    )
    page_divs = []
    for key, icon, label, render_fn in PAGES:
        print(f"  - {label} 생성 중...", flush=True)
        content = render_fn()
        page_divs.append(
            f'<div class="page" id="page-{key}">'
            f'<h1>{icon} {label}</h1>'
            f'{content}'
            f'</div>'
        )
    generated_at = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
    js = JS.replace("__HIST_START__", str(HIST_START_D)).replace("__MAX_D__", str(MAX_D))
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>채권/IRS 트레이딩 대시보드</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2/plotly.min.js"></script>
<script>{js}</script>
<style>{CSS}</style>
</head>
<body>
<div id="layout">
  <nav id="sidebar">
    <div class="brand">채권/IRS 대시보드</div>
    {nav_links}
  </nav>
  <main id="main">
    <div id="top-note">최종 갱신: {generated_at} (매일 자동 갱신) · {NOTE_SIMPLIFIED}</div>
    {''.join(page_divs)}
  </main>
</div>
</body>
</html>"""


def main():
    out_dir = REPO_DIR / "docs"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)
    src_img = app.ASSETS_DIR / "trilemma_diagram.png"
    if src_img.exists():
        shutil.copy(src_img, out_dir / "assets" / "trilemma_diagram.png")

    print("정적 사이트 생성 시작...")
    html = build()
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    size_mb = len(html.encode("utf-8")) / 1024 / 1024
    print(f"완료: {out_dir / 'index.html'} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
