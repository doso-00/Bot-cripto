"""Strumenti comuni ai report HTML: formati, tabelle, stile dei grafici, CSS."""

import html
import math

import pandas as pd
import plotly.graph_objects as go

# Palette categoriale di riferimento (slot 1-3, validati anche a coppie su sfondo chiaro)
SERIES_COLORS = ["#2a78d6", "#1baf7a", "#eb6834"]
SURFACE, TEXT, TEXT_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"


def pct(x, digits=2) -> str:
    return "n/d" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.{digits}f}%"


def num(x, digits=2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/d"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:,.{digits}f}".replace(",", " ")


def duration(td) -> str:
    if td is None or pd.isna(td):
        return "n/d"
    return f"{td.days} giorni" if td.days >= 1 else f"{td.total_seconds() / 3600:.0f} ore"


def table(headers: list[str], rows: list[list], numeric_from: int = 1) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(
            f"<td class='{'num' if i >= numeric_from else ''}'>{html.escape(str(v))}</td>" for i, v in enumerate(r)
        ) + "</tr>"
        for r in rows
    )
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def layout(fig: go.Figure, title: str, y_title: str, y_format: str | None = None, log_y: bool = False) -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"size": 15, "color": TEXT}, "x": 0},
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, font={"color": TEXT_2, "size": 12},
        hovermode="x unified", margin={"l": 60, "r": 20, "t": 60, "b": 40}, height=380,
        legend={"orientation": "h", "y": 1.02, "x": 0, "yanchor": "bottom"},
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID)
    fig.update_yaxes(title=y_title, gridcolor=GRID, zeroline=False,
                     tickformat=",.0f" if log_y else y_format,  # su scala log niente etichette abbreviate (2, 3, ...)
                     type="log" if log_y else "linear")
    return fig


CSS = f"""
:root {{ color-scheme: light; --surface:{SURFACE}; --text:{TEXT}; --text-2:{TEXT_2}; --grid:{GRID};
        --warn-bg:#fff4e0; --warn-border:#eda100; --ok-bg:#e8f6ef; --ok-border:#1baf7a; }}
body {{ margin:0; background:var(--surface); color:var(--text); font:14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:32px 0 8px; }}
.meta {{ color:var(--text-2); font-size:13px; }}
.box {{ border-left:4px solid var(--warn-border); background:var(--warn-bg); padding:8px 12px; margin:8px 0; }}
.box.ok {{ border-color:var(--ok-border); background:var(--ok-bg); }}
.table-wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ padding:6px 10px; border-bottom:1px solid var(--grid); text-align:left; white-space:nowrap; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
th {{ color:var(--text-2); font-weight:600; white-space:normal; vertical-align:bottom; min-width:64px; }}
.scroll {{ max-height:420px; overflow-y:auto; }}
ul {{ margin:4px 0; padding-left:20px; }}
"""
