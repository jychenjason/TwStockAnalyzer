"""Chart generation module using Plotly for Taiwan stock visualization."""

import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import pandas as pd


#: 台股慣例：收紅為漲、收綠為跌。與 plot_interactive_kline 的既有配色一致。
UP_COLOR = "#E74C3C"
DOWN_COLOR = "#2ECC71"


def volume_colors(df: pd.DataFrame) -> list[str]:
    """成交量柱的顏色，逐日對應該日 K 棒的紅綠。

    判斷規則與 Plotly 對蠟燭的分類相同（收盤 >= 開盤為漲），兩者才會永遠同步；
    十字線（收盤等於開盤）歸為漲，與蠟燭的畫法一致。
    """
    return [
        UP_COLOR if close >= open_ else DOWN_COLOR
        for open_, close in zip(df["open"], df["close"])
    ]


def non_trading_days(dates) -> list[str]:
    """在資料涵蓋的期間內、沒有交易的日曆日。

    週末、國定假日與停牌日一律涵蓋——判斷依據是「這一天在資料裡不存在」，
    而不是星期幾，所以停牌與連假不需要另外處理。
    """
    present = pd.to_datetime(pd.Series(list(dates)).dropna().unique())
    if len(present) == 0:
        return []
    calendar = pd.date_range(present.min(), present.max(), freq="D")
    missing = calendar.difference(present)
    return [d.strftime("%Y-%m-%d") for d in missing]


def _hide_non_trading_days(fig: go.Figure, dates) -> None:
    """把非交易日從時間軸上拿掉，K 棒才不會被一段段空白撐開。"""
    missing = non_trading_days(dates)
    if missing:
        fig.update_xaxes(rangebreaks=[dict(values=missing)])


def plot_kline(df: pd.DataFrame, indicators: dict = None) -> go.Figure:
    """Create candlestick chart with optional indicator overlays.

    Args:
        df: DataFrame with columns: date, open, high, low, close, volume.
        indicators: Dict mapping column names to colors, e.g.
            {"ma_5": "red", "ma_20": "blue"}.

    Returns:
        Plotly Figure with K-line and volume subplots.
    """
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
        subplot_titles=("K 線", "成交量"),
    )

    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="價格",
            increasing_line_color=UP_COLOR,
            increasing_fillcolor=UP_COLOR,
            decreasing_line_color=DOWN_COLOR,
            decreasing_fillcolor=DOWN_COLOR,
        ),
        row=1, col=1,
    )

    if indicators:
        for col_name, color in indicators.items():
            if col_name in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df["date"],
                        y=df[col_name],
                        name=col_name,
                        line=dict(color=color, width=1),
                    ),
                    row=1, col=1,
                )

    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=df["volume"],
            name="成交量",
            marker_color=volume_colors(df),
        ),
        row=2, col=1,
    )

    fig.update_layout(
        height=600,
        title_text="K 線圖",
        xaxis_rangeslider_visible=False,
        template="plotly_white",
    )
    fig.update_xaxes(title_text="日期", row=2, col=1)
    fig.update_yaxes(title_text="價格 (元)", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    _hide_non_trading_days(fig, df["date"])

    return fig


def plot_technical(df: pd.DataFrame) -> go.Figure:
    """Multi-panel subplot: price + volume + RSI + MACD + KD.

    Args:
        df: DataFrame with columns: date, open, high, low, close, volume,
            and optionally rsi_14, macd_*, kd_k.

    Returns:
        Plotly Figure with 5-row technical analysis chart.
    """
    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.35, 0.15, 0.15, 0.15, 0.2],
        subplot_titles=("價格", "成交量", "RSI", "MACD", "KD"),
    )

    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="價格",
            increasing_line_color=UP_COLOR,
            increasing_fillcolor=UP_COLOR,
            decreasing_line_color=DOWN_COLOR,
            decreasing_fillcolor=DOWN_COLOR,
        ),
        row=1, col=1,
    )

    fig.add_trace(
        go.Bar(
            x=df["date"],
            y=df["volume"],
            name="成交量",
            marker_color=volume_colors(df),
        ),
        row=2, col=1,
    )

    if "rsi_14" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["rsi_14"],
                name="RSI(14)",
                line=dict(color="orange"),
            ),
            row=3, col=1,
        )

    macd_cols = [c for c in df.columns if c.startswith("macd_")]
    for col in macd_cols:
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df[col],
                name=col,
                line=dict(width=1),
            ),
            row=4, col=1,
        )

    for col in ("kd_k", "kd_d"):
        if col in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df[col],
                    name=col.upper(),
                    line=dict(width=1),
                ),
                row=5, col=1,
            )

    fig.update_layout(
        height=800,
        title_text="技術分析圖",
        template="plotly_white",
    )

    _hide_non_trading_days(fig, df["date"])

    return fig


def save_chart(fig: go.Figure, output_path: str, format: str = "html") -> str:
    """Save chart to HTML or PNG.

    Args:
        fig: Plotly Figure to save.
        output_path: Destination file path.
        format: "html" or "png".

    Returns:
        The output_path.
    """
    if format == "html":
        fig.write_html(output_path)
    elif format == "png":
        fig.write_image(output_path)
    return output_path


def plot_interactive_kline(df: pd.DataFrame, stock_id: str = "") -> str:
    """Generate a standalone interactive K-line HTML report.

    Features:
        - Candlestick chart with EMA 5/10/20 overlays
        - Volume, RSI, MACD, KD sub-panels
        - Range slider with dynamic Y-axis (autorange on visible data)
        - Gap-free axis via rangebreaks for all missing dates
        - Toggle switches for each MA line and each sub-panel

    Args:
        df: DataFrame with columns: date, open, high, low, close, volume,
            plus optional rsi_14, macd_*, kd_k, kd_d, ema_5/10/20.
        stock_id: Stock ticker for the title.

    Returns:
        Complete HTML string for the interactive report.
    """
    # ── compute missing dates (non-trading days) ──
    rangebreaks = [dict(values=non_trading_days(df["date"]))]

    # ── build figure ──
    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.40, 0.15, 0.15, 0.15, 0.15],
    )

    # row 1 – candlestick
    fig.add_trace(
        go.Candlestick(
            x=df["date"], open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name="Price",
            increasing_line_color=UP_COLOR,
            increasing_fillcolor=UP_COLOR,
            decreasing_line_color=DOWN_COLOR,
            decreasing_fillcolor=DOWN_COLOR,
            hoverinfo="none",
        ),
        row=1, col=1,
    )

    # row 1 – EMAs
    ema_specs = [
        ("ema_5",  "#4A90D9", "EMA5"),
        ("ema_10", "#FF8C00", "EMA10"),
        ("ema_20", "#9B59B6", "EMA20"),
    ]
    ema_idx = []
    for col, color, label in ema_specs:
        if col in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["date"], y=df[col],
                    name=label,
                    line=dict(color=color, width=1.5),
                    hoverinfo="none",
                ),
                row=1, col=1,
            )
            ema_idx.append(len(fig.data) - 1)

    # row 2 – volume
    fig.add_trace(
        go.Bar(
            x=df["date"], y=df["volume"],
            name="Volume",
            marker_color=volume_colors(df),
            hoverinfo="none",
        ),
        row=2, col=1,
    )
    vol_idx = len(fig.data) - 1

    # row 3 – RSI
    rsi_idx = None
    if "rsi_14" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["date"], y=df["rsi_14"],
                name="RSI(14)",
                line=dict(color="#E67E22", width=1.5),
                hoverinfo="none",
            ),
            row=3, col=1,
        )
        rsi_idx = len(fig.data) - 1

    # row 4 – MACD
    macd_cols = sorted(c for c in df.columns if c.startswith("macd_"))
    macd_idx = []
    for col in macd_cols:
        label = col.replace("macd_", "")
        fig.add_trace(
            go.Scatter(
                x=df["date"], y=df[col],
                name=f"MACD({label})",
                line=dict(width=1.2),
                hoverinfo="none",
            ),
            row=4, col=1,
        )
        macd_idx.append(len(fig.data) - 1)

    # row 5 – KD
    kd_cols = [c for c in ("kd_k", "kd_d") if c in df.columns]
    kd_idx = []
    for col in kd_cols:
        fig.add_trace(
            go.Scatter(
                x=df["date"], y=df[col],
                name=col.upper(),
                line=dict(width=1.2),
                hoverinfo="none",
            ),
            row=5, col=1,
        )
        kd_idx.append(len(fig.data) - 1)

    # dummy trace on row 5 for rangeslider date overview
    fig.add_trace(
        go.Scatter(
            x=df["date"], y=[0]*len(df),
            name="",
            showlegend=False,
            hoverinfo="none",
            line=dict(color="rgba(0,0,0,0)", width=0),
        ),
        row=5, col=1,
    )
    date_range_idx = len(fig.data) - 1

    # ── layout ──
    fig.update_layout(
        height=800,
        title_text=f"{stock_id} Interactive K-Line Chart" if stock_id else "Interactive K-Line Chart",
        template="plotly_white",
        hovermode="x",
        spikedistance=-1,
        dragmode="zoom",
        margin=dict(l=50, r=30, t=70, b=40),
        legend=dict(orientation="h", y=1.02, x=0.5, xanchor="center", font=dict(size=10)),
    hoverlabel=dict(
        bgcolor="rgba(0,0,0,0)",
        font_size=12,
        bordercolor="#ccc",
    ),
    )

    # x-axes
    for r in range(1, 6):
        fig.update_xaxes(
            rangebreaks=rangebreaks,
            showspikes=True,
            spikemode="across",
            spikethickness=1,
            spikedash="dash",
            spikecolor="rgba(180,180,180,0.4)",
            row=r, col=1,
        )
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    fig.update_xaxes(
        rangeslider_visible=True,
        rangeslider_thickness=0.08,
        row=5, col=1,
    )

    # y-axes
    fig.update_yaxes(title_text="Price (TWD)", autorange=True, row=1, col=1)
    fig.update_yaxes(title_text="Volume", rangemode="tozero", row=2, col=1)
    fig.update_yaxes(title_text="RSI", row=3, col=1)
    fig.update_yaxes(title_text="MACD", row=4, col=1)
    if kd_cols:
        fig.update_yaxes(title_text="KD", row=5, col=1)

    for r in range(1, 6):
        fig.update_yaxes(
            fixedrange=True,
            showspikes=False,
            row=r, col=1,
        )

    # ── partial HTML ──
    plot_html = pio.to_html(
        fig,
        include_plotlyjs=False,
        full_html=False,
        div_id="kline-chart",
        config={
            "responsive": True,
            "displaylogo": False,
            "modeBarButtonsToRemove": [
                "zoom2d", "pan2d", "select2d", "lasso2d",
                "zoomIn2d", "zoomOut2d",
                "autoScale2d", "resetScale2d",
            ],
            "modeBarButtonsToAdd": ["drawline", "eraseshape"],
        },
    )

    # ── trace index bookkeeping for JS ──
    n_ema = len(ema_idx)
    n_macd = len(macd_idx)
    n_kd = len(kd_idx)
    has_rsi = rsi_idx is not None
    rsi_anchor = rsi_idx if rsi_idx is not None else "null"
    macd_anchor = macd_idx[0] if macd_idx else "null"
    kd_anchor = kd_idx[0] if kd_idx else "null"

    # ── assemble full HTML ──
    title = f"{stock_id} Interactive K-Line" if stock_id else "Interactive K-Line"

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://cdn.plot.ly/plotly-3.7.0.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f6fa;color:#2c3e50}}
#header{{background:#fff;border-bottom:1px solid #ddd;padding:10px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
#header h1{{font-size:16px;font-weight:600}}
.toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:6px 0}}
.toolbar-group{{display:flex;align-items:center;gap:4px;padding:3px 10px 3px 8px;border-radius:6px;font-size:13px;cursor:pointer;user-select:none;border:1px solid #ddd;background:#fff}}
.toolbar-group:hover{{border-color:#bbb}}
.toolbar-group input[type=checkbox]{{margin:0;cursor:pointer;accent-color:#555}}
.toolbar-group label{{cursor:pointer;white-space:nowrap;margin-left:3px}}
.toolbar-divider{{width:1px;height:22px;background:#ddd}}
#hover-info{{background:#fff;border-bottom:1px solid #e8e8e8;padding:6px 20px;font-size:13px;display:flex;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:#555;min-height:28px;align-items:center;gap:16px;flex-wrap:wrap}}
#hover-info .item{{white-space:nowrap}}
#hover-info .label{{color:#999}}
#hover-info .value{{font-weight:600;margin-left:3px}}
#hover-info.show{{display:flex}}

#kline-chart{{width:100%}}
</style>
</head>
<body>

<div id="header">
  <h1>{title}</h1>
  <div class="toolbar">
    <span style="font-size:12px;color:#888;margin-right:2px">均線</span>
    <div class="toolbar-group" id="tgl-ema5">
      <input type="checkbox" checked><label style="color:#4A90D9;font-weight:600">MA5</label>
    </div>
    <div class="toolbar-group" id="tgl-ema10">
      <input type="checkbox" checked><label style="color:#FF8C00;font-weight:600">MA10</label>
    </div>
    <div class="toolbar-group" id="tgl-ema20">
      <input type="checkbox" checked><label style="color:#9B59B6;font-weight:600">MA20</label>
    </div>
    <div class="toolbar-divider"></div>
    <span style="font-size:12px;color:#888;margin-right:2px">子圖</span>
    <div class="toolbar-group" id="tgl-sub-kline">
      <input type="checkbox" checked><label>K線</label>
    </div>
    <div class="toolbar-group" id="tgl-sub-volume">
      <input type="checkbox" checked><label>量</label>
    </div>
    <div class="toolbar-group" id="tgl-sub-rsi">
      <input type="checkbox"><label>RSI</label>
    </div>
    <div class="toolbar-group" id="tgl-sub-macd">
      <input type="checkbox"><label>MACD</label>
    </div>
    <div class="toolbar-group" id="tgl-sub-kd">
      <input type="checkbox"><label>KD</label>
    </div>
    <div class="toolbar-divider"></div>
    <div class="toolbar-group" id="tgl-crosshair">
      <input type="checkbox" checked><label>十字</label>
    </div>
  </div>
</div>

<div id="hover-info"></div>

{plot_html}

<script>
(function(){{
var gd = document.getElementById('kline-chart');
var nEMA = {n_ema};
var nMACD = {n_macd};
var nKD = {n_kd};
var dateRangeIdx = {date_range_idx};
var hasRSI = {str(has_rsi).lower()};

// trace index helpers
function Tgl(){{
  this.ma = {{ema5:true, ema10:true, ema20:true}};
  this.sub = {{kline:true, volume:true, rsi:false, macd:false, kd:false}};
  this.crosshair = true;
}}
var st = new Tgl();

// ---------- shared row/subplot metadata ----------
// row order 1..5 == K-line, Volume, RSI, MACD, KD, matching the Python
// make_subplots(rows=5, ...) call this HTML was generated from.
var ROW_KEYS = ['kline','volume','rsi','macd','kd'];
var ROW_HEIGHTS = [0.40, 0.15, 0.15, 0.15, 0.15];
var V_SPACING = 0.035;
var DOMAIN_EPS = 0.001;
var ROW_YAXIS_KEY = {{1:'yaxis', 2:'yaxis2', 3:'yaxis3', 4:'yaxis4', 5:'yaxis5'}};
var ROW_SUBPLOT_ID = {{1:'xy', 2:'x2y2', 3:'x3y3', 4:'x4y4', 5:'x5y5'}};
var ROW_ANCHOR_TRACE = {{1:0, 2:{vol_idx}, 3:{rsi_anchor}, 4:{macd_anchor}, 5:{kd_anchor}}};

// capture each row's original y-axis title, so a collapsed/restored row
// can have it hidden/restored correctly.
var ROW_YTITLES = {{}};
(function(){{
  for (var i=0; i<5; i++) {{
    var row = i+1;
    var yAxis = gd.layout[ROW_YAXIS_KEY[row]];
    ROW_YTITLES[row] = (yAxis.title && yAxis.title.text) ? yAxis.title.text : '';
  }}
}})();

// redistribute the 5 rows' y-domains across only the currently-enabled
// (st.sub) rows, so a disabled subplot collapses to zero height instead of
// leaving a blank panel, and the remaining rows grow to fill the freed space.
function computeRowDomains() {{
  var enabled = ROW_KEYS.map(function(k){{ return !!st.sub[k]; }});
  var n = 0;
  for (var i=0; i<5; i++) if (enabled[i]) n++;
  if (n===0) return null; // guard: never collapse every row
  var totalHeight = 0;
  for (var i=0; i<5; i++) if (enabled[i]) totalHeight += ROW_HEIGHTS[i];
  var usable = 1 - (n-1)*V_SPACING;
  var cursor = 1.0;
  var first = true;
  var domains = {{}};
  for (var i=0; i<5; i++) {{
    var row = i+1;
    // a truly zero-height domain (top===bottom) corrupts Plotly's shared
    // grid geometry computation for the WHOLE figure, not just this axis
    // (all other rows can go blank) - use an imperceptible sliver instead.
    // Clamp into [0,1]: if cursor is already at 0 (e.g. every other row is
    // disabled too), "cursor - DOMAIN_EPS" would go negative and be just as
    // degenerate, so anchor the sliver above cursor instead in that case.
    if (!enabled[i]) {{
      var epsBottom = Math.max(0, cursor - DOMAIN_EPS);
      var epsTop = Math.min(1, epsBottom + DOMAIN_EPS);
      domains[row] = [epsBottom, epsTop];
      continue;
    }}
    if (!first) cursor -= V_SPACING;
    var frac = (ROW_HEIGHTS[i]/totalHeight) * usable;
    var top = cursor, bottom = cursor - frac;
    domains[row] = [bottom, top];
    cursor = bottom;
    first = false;
  }}
  return domains;
}}

function applyRowDomains() {{
  var domains = computeRowDomains();
  if (!domains) return;
  var upd = {{}};
  for (var row=1; row<=5; row++) {{
    var i = row-1;
    var on = st.sub[ROW_KEYS[i]];
    var key = ROW_YAXIS_KEY[row];
    upd[key+'.domain'] = domains[row];
    // a zero-height domain still renders its ticks/gridlines/title unless
    // explicitly hidden, so a collapsed row would otherwise leave a stray
    // cluster of tick labels overlapping the row above it.
    upd[key+'.showticklabels'] = on;
    upd[key+'.showgrid'] = on;
    upd[key+'.zeroline'] = on;
    upd[key+'.title.text'] = on ? ROW_YTITLES[row] : '';
  }}
  Plotly.relayout(gd, upd);
}}

// ---------- MA toggles ----------
var maIDs = ['tgl-ema5','tgl-ema10','tgl-ema20'];
var maKeys = ['ema5','ema10','ema20'];
for(var m=0; m<3; m++) {{
  (function(idx,key){{
    var el = document.getElementById(maIDs[idx]);
    if(!el) return;
    var cb = el.querySelector('input[type=checkbox]');
    cb.addEventListener('change',function(){{
      var v = cb.checked; st.ma[key]=v;
      if(st.sub.kline) {{
        // trace 1..nEMA are the MA lines
        Plotly.restyle(gd,{{visible:v}},[idx+1]);
      }}
    }});
  }})(m,maKeys[m]);
}}

// ---------- subplot toggles ----------
// trace layout:
//   0: candlestick (kline)
//   1..nEMA: EMAs (kline)
//   nEMA+1: volume
//   nEMA+2: rsi (if hasRSI)
//   nEMA+2..nEMA+2+nMACD-1: macd traces
//   nEMA+2+nMACD..nEMA+2+nMACD+nKD-1: kd traces
//   dateRangeIdx: dummy trace on row 5 (rangeslider date backbone, always visible)

var subSpecs = [
  {{id:'tgl-sub-kline',key:'kline',traces:function(){{
    var arr=[0]; for(var i=1;i<=nEMA;i++) arr.push(i); return arr;
  }},visFn:function(){{
    var arr=[true]; for(var i=1;i<=nEMA;i++) arr.push(st.ma[['ema5','ema10','ema20'][i-1]]);
    return arr;
  }}}},
  {{id:'tgl-sub-volume',key:'volume',traces:[1+nEMA],visFn:function(){{return [true]}}}},
  {{id:'tgl-sub-rsi',key:'rsi',traces:function(){{
    return hasRSI ? [2+nEMA] : [];
  }},visFn:function(){{return [true]}}}},
  {{id:'tgl-sub-macd',key:'macd',traces:function(){{
    var start = 2+nEMA+(hasRSI?1:0);
    var arr=[]; for(var i=0;i<nMACD;i++) arr.push(start+i); return arr;
  }},visFn:function(){{return [true]}}}},
  {{id:'tgl-sub-kd',key:'kd',traces:function(){{
    var start = 2+nEMA+(hasRSI?1:0)+nMACD;
    var arr=[]; for(var i=0;i<nKD;i++) arr.push(start+i); return arr;
  }},visFn:function(){{return [true]}}}},
];

function getTraces(spec) {{
  return typeof spec.traces==='function' ? spec.traces() : spec.traces;
}}

for(var s=0; s<subSpecs.length; s++) {{
  (function(spec){{
    var el = document.getElementById(spec.id);
    if(!el) return;
    var cb = el.querySelector('input[type=checkbox]');
    cb.addEventListener('change',function(){{
      var v = cb.checked; st.sub[spec.key]=v;
      var traces = getTraces(spec);
      if(traces.length>0) {{
        if(!v) {{
          Plotly.restyle(gd,{{visible:false}},traces);
        }} else {{
          var vis = spec.visFn();
          if(vis.length===1 && traces.length>1) {{
            Plotly.restyle(gd,{{visible:vis[0]}},traces);
          }} else {{
            // build per-trace visibility
            var fullVis = [];
            for(var i=0;i<traces.length;i++) {{
              fullVis.push(vis[i]!==undefined ? vis[i] : true);
            }}
            Plotly.restyle(gd,{{visible:fullVis}},traces);
          }}
        }}
      }}
      // collapse/restore the row's own y-domain and re-flow the other rows
      // to fill the freed space, rather than leaving a blank panel.
      applyRowDomains();
    }});
  }})(subSpecs[s]);
}}

// ---------- apply initial visibility ----------
for(var s=0; s<subSpecs.length; s++) {{
  var spec = subSpecs[s];
  if(!st.sub[spec.key]) {{
    var traces = getTraces(spec);
    if(traces.length>0) Plotly.restyle(gd,{{visible:false}},traces);
  }}
}}
applyRowDomains();

// ---------- crosshair toggle ----------
(function(){{
  var el = document.getElementById('tgl-crosshair');
  if(!el) return;
  var cb = el.querySelector('input[type=checkbox]');
  var infoDiv = document.getElementById('hover-info');
  cb.addEventListener('change',function(){{
    st.crosshair = cb.checked;
    if (!st.crosshair) Plotly.Fx.unhover(gd);
  }});
}})();

// ---------- dynamic Y-axis rescale on visible x-range change ----------
(function(){{
  var fullXRange = [gd.data[0].x[0], gd.data[0].x[gd.data[0].x.length-1]];
  var isRescaling = false;
  var PAD = 0.05;
  var rowSpec = {{1:subSpecs[0], 2:subSpecs[1], 3:subSpecs[2], 4:subSpecs[3], 5:subSpecs[4]}};

  function computeRange(traceIdxs, xStart, xEnd, isCandleRow) {{
    var lo = Infinity, hi = -Infinity;
    for (var t=0; t<traceIdxs.length; t++) {{
      // gd._fullData holds Plotly's decoded/normalized arrays (safe to index);
      // gd.data may hold bdata-compacted arrays that aren't plain-indexable.
      var tr = gd._fullData[traceIdxs[t]];
      if (!tr || tr.visible===false || tr.visible==='legendonly') continue;
      var xs = tr.x;
      for (var i=0; i<xs.length; i++) {{
        if (xs[i] < xStart || xs[i] > xEnd) continue;
        if (isCandleRow && tr.type==='candlestick') {{
          var h = tr.high[i], l = tr.low[i];
          if (h!=null && !isNaN(h) && h>hi) hi=h;
          if (l!=null && !isNaN(l) && l<lo) lo=l;
        }} else if (tr.y) {{
          var yv = tr.y[i];
          if (yv==null || isNaN(yv)) continue;
          if (yv>hi) hi=yv;
          if (yv<lo) lo=yv;
        }}
      }}
    }}
    return (lo===Infinity || hi===-Infinity) ? null : [lo, hi];
  }}

  function rangeWithPadding(range, pad, forceZeroFloor) {{
    if (!range) return null;
    var lo=range[0], hi=range[1];
    var span = hi-lo; if (span===0) span = Math.abs(hi)||1;
    var paddedLo = lo - span*pad, paddedHi = hi + span*pad;
    if (forceZeroFloor) paddedLo = 0;
    return [paddedLo, paddedHi];
  }}

  function rescaleYAxes(xStart, xEnd) {{
    var relayoutUpdate = {{}};
    for (var r=1; r<=5; r++) {{
      var spec = rowSpec[r];
      var traceIdxs = getTraces(spec);
      if (traceIdxs.length===0 || !st.sub[spec.key]) continue;
      var range = computeRange(traceIdxs, xStart, xEnd, r===1);
      var padded = rangeWithPadding(range, PAD, r===2);
      if (!padded) continue;
      relayoutUpdate[ROW_YAXIS_KEY[r]+'.range'] = padded;
      relayoutUpdate[ROW_YAXIS_KEY[r]+'.autorange'] = false;
    }}
    if (Object.keys(relayoutUpdate).length===0) return;
    isRescaling = true;
    Plotly.relayout(gd, relayoutUpdate).then(function(){{ isRescaling = false; }});
  }}

  var isClampingZoom = false;

  function toTime(v) {{
    return typeof v==='string' ? new Date(v).getTime() : v.getTime();
  }}

  function clampXRange(xStart, xEnd) {{
    var xs = gd._fullData[0].x;
    if (!xs || xs.length===0) return null;
    var t0 = toTime(xStart);
    var t1 = toTime(xEnd);
    var d0 = toTime(xs[0]), d1 = toTime(xs[xs.length-1]);
    var c0 = Math.max(t0, d0), c1 = Math.min(t1, d1);
    function idxOf(t) {{
      var lo=0, hi=xs.length-1;
      while (lo<hi-1) {{ var m=(lo+hi)>>1; if (toTime(xs[m])<t) lo=m; else hi=m; }}
      return Math.abs(toTime(xs[lo])-t)<=Math.abs(toTime(xs[hi])-t)?lo:hi;
    }}
    var i0 = idxOf(c0), i1 = idxOf(c1);
    if (i1-i0+1 < 5) {{
      var mid = (i0+i1)>>1;
      i0 = Math.max(0, mid-2);
      i1 = Math.min(xs.length-1, mid+2);
      if (i1-i0+1<5) {{ if(i0===0) i1=Math.min(xs.length-1,4); else i0=Math.max(0,xs.length-5); }}
    }}
    return [xs[i0], xs[i1]];
  }}

  gd.on('plotly_relayout', function(evt){{
    if (isRescaling) return;
    var xStart, xEnd;
    if (evt['xaxis.autorange']) {{ xStart = fullXRange[0]; xEnd = fullXRange[1]; }}
    else if (evt['xaxis.range']) {{ xStart = evt['xaxis.range'][0]; xEnd = evt['xaxis.range'][1]; }}
    else if (evt['xaxis.range[0]'] !== undefined) {{ xStart = evt['xaxis.range[0]']; xEnd = evt['xaxis.range[1]']; }}
    else return;
    if (!isClampingZoom) {{
      var clamped = clampXRange(xStart, xEnd);
      if (clamped) {{
        var ct0 = toTime(clamped[0]), ct1 = toTime(clamped[1]);
        var ot0 = toTime(xStart);
        var ot1 = toTime(xEnd);
        if (ct0!==ot0 || ct1!==ot1) {{
          isClampingZoom = true;
          Plotly.relayout(gd, {{'xaxis.range': [clamped[0], clamped[1]]}});
          isClampingZoom = false;
          xStart = clamped[0];
          xEnd = clamped[1];
        }}
      }}
    }}
    rescaleYAxes(xStart, xEnd);
  }});
}})();

// ---------- hover info bar + synced crosshair across subplots ----------
(function(){{
  var infoDiv = document.getElementById('hover-info');
  var EMPTY_INFO = {{date:'--', open:'--', high:'--', low:'--', close:'--', volume:'--'}};
  var processedKeys = {{}};
  var unhoverTimer = null;

  function fmtPrice(v) {{
    return (v==null || isNaN(v)) ? '--' : Number(v).toFixed(1);
  }}

  function renderInfoBar(info) {{
    infoDiv.innerHTML = '';
    var items = [
      ['日期', info.date], ['開', info.open], ['高', info.high],
      ['低', info.low], ['收', info.close], ['量', info.volume],
    ];
    for (var i=0; i<items.length; i++) {{
      var span = document.createElement('span');
      span.className = 'item';
      span.innerHTML = '<span class="label">'+items[i][0]+'</span><span class="value">'+items[i][1]+'</span>';
      infoDiv.appendChild(span);
    }}
  }}
  renderInfoBar(EMPTY_INFO);

  function rowOfSubplot(xaxisId, yaxisId) {{
    var key = xaxisId + yaxisId;
    for (var row=1; row<=5; row++) {{
      if (ROW_SUBPLOT_ID[row]===key) return row;
    }}
    return null;
  }}

  function syncCrosshair(idx) {{
    if (!st.crosshair || idx===undefined) return;
    var xval = gd._fullData[0].x[idx];
    if (xval===undefined) return;
    var subplots = [];
    for (var row=1; row<=5; row++) {{
      if (!st.sub[ROW_KEYS[row-1]]) continue;
      var anchor = ROW_ANCHOR_TRACE[row];
      if (anchor===null || anchor===undefined) continue;
      var tr = gd._fullData[anchor];
      if (!tr || tr.visible===false || tr.visible==='legendonly') continue;
      subplots.push(ROW_SUBPLOT_ID[row]);
    }}
    if (subplots.length===0) return;
    Plotly.Fx.hover(gd, [{{x: xval}}], subplots);
  }}

  function cancelPendingUnhover() {{
    if (unhoverTimer) {{ clearTimeout(unhoverTimer); unhoverTimer = null; }}
  }}

  var XHAIR_CLASS = 'custom-xhair';

  function removeHorizontalLines() {{
    var els = document.querySelectorAll('.'+XHAIR_CLASS);
    for (var i=0; i<els.length; i++) els[i].remove();
  }}

  function drawHorizontalLines(idx) {{
    if (!st.crosshair || idx===undefined) return;
    removeHorizontalLines();
    var fullLayout = gd._fullLayout;
    var xaxis = fullLayout.xaxis;
    var plotLeft = xaxis._offset;
    var plotWidth = xaxis._length;
    var mainSvg = gd.querySelector('.main-svg');
    if (!mainSvg) return;
    var ns = 'http://www.w3.org/2000/svg';
    for (var row=1; row<=5; row++) {{
      if (!st.sub[ROW_KEYS[row-1]]) continue;
      var yKey = ROW_YAXIS_KEY[row];
      var yaxis = fullLayout[yKey];
      if (!yaxis) continue;
      var anchor = ROW_ANCHOR_TRACE[row];
      if (anchor===null || anchor===undefined) continue;
      var tr = gd._fullData[anchor];
      if (!tr || tr.visible===false || tr.visible==='legendonly') continue;
      var yv;
      if (row===1 && tr.type==='candlestick') {{
        yv = tr.close[idx];
      }} else if (tr.y) {{
        yv = tr.y[idx];
      }}
      if (yv==null || isNaN(yv)) continue;
      // pixel position relative to the SVG canvas
      var yPx = yaxis.d2p(yv);
      var subplotId = ROW_SUBPLOT_ID[row];
      var subplotGroup = mainSvg.querySelector('.subplot.'+subplotId);
      if (!subplotGroup) continue;
      var below = subplotGroup.querySelector('.layer-below');
      if (!below) continue;
      var yLocal = yPx;
      var line = document.createElementNS(ns, 'line');
      line.setAttribute('class', XHAIR_CLASS);
      line.setAttribute('x1', 0);
      line.setAttribute('y1', yLocal);
      line.setAttribute('x2', plotWidth);
      line.setAttribute('y2', yLocal);
      line.setAttribute('stroke', 'rgba(180,180,180,0.45)');
      line.setAttribute('stroke-width', '1');
      line.setAttribute('stroke-dasharray', '4,4');
      below.appendChild(line);
    }}
  }}

  var drewLines = false;

  gd.on('plotly_hover',function(evt){{
    if(!st.crosshair) return;
    var pts = evt.points;
    if(!pts||pts.length===0) return;
    var d = pts[0];
    var idx = d.pointIndex;
    if(idx===undefined) return;
    var originRow = rowOfSubplot(d.xaxis._id, d.yaxis._id);
    if (originRow===null) return;
    var key = originRow + ':' + idx;
    if (processedKeys[key]) return;
    processedKeys[key] = true;
    drewLines = false;
    cancelPendingUnhover();
    var cp = null;
    for(var p=0;p<pts.length;p++) {{
      if(pts[p].curveNumber===0) {{ cp=pts[p]; break; }}
    }}
    if(!cp) cp = pts[0];
    var full = gd._fullData[0];
    var dateStr = cp.x;
    if(dateStr instanceof Date) dateStr = dateStr.toISOString().slice(0,10);
    var o = full.open[idx], h = full.high[idx], l = full.low[idx], c = full.close[idx];
    var volTrace = gd._fullData[{vol_idx}];
    var volRaw = volTrace && volTrace.y ? volTrace.y[idx] : undefined;
    renderInfoBar({{
      date: dateStr,
      open: fmtPrice(o), high: fmtPrice(h), low: fmtPrice(l), close: fmtPrice(c),
      volume: (volRaw==null || isNaN(volRaw)) ? '--' : Number(volRaw).toLocaleString(),
    }});
    if (!drewLines) {{
      drawHorizontalLines(idx);
      drewLines = true;
    }}
    syncCrosshair(idx);
  }});
  gd.on('plotly_unhover',function(){{
    if(!st.crosshair) return;
    cancelPendingUnhover();
    unhoverTimer = setTimeout(function(){{
      processedKeys = {{}};
      renderInfoBar(EMPTY_INFO);
      removeHorizontalLines();
      Plotly.Fx.unhover(gd);
      unhoverTimer = null;
    }}, 120);
  }});
}})();

}})();
</script>
</body>
</html>"""
    return html
