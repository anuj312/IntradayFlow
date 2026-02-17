# contribution.py
# Sector Contribution page plugin for your main Dash app (app.py)
#
# Exposes:
#   contribution_page(BASE) -> layout
#   register_contribution(dash_app, BASE, ctx) -> registers callbacks
#
# ctx must include:
#   LOCK, IST, SECTOR_DEFINITIONS, symbol_to_token, DAILY_STATS, compute_rfactor_row_for_token

from datetime import datetime
from typing import Dict, Any, List, Tuple

import pandas as pd
import dash
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc
import dash_ag_grid as dag

TOP_N = 3


def _time_factor_ist(now_ist: datetime) -> float:
    """Fraction of session completed (9:15-15:30). Clamped."""
    m_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    m_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

    total_mins = 375.0
    if now_ist < m_open:
        mins_passed = 1.0
    elif now_ist > m_close:
        mins_passed = total_mins
    else:
        mins_passed = max(1.0, (now_ist - m_open).total_seconds() / 60.0)

    tf = mins_passed / total_mins
    return max(0.01, min(1.0, float(tf)))


def _fmt_top(lst: List[dict], n: int = TOP_N) -> str:
    if not lst:
        return "—"
    return ", ".join([f"{r['Symbol']} {r['RVOLm']:.2f}x" for r in lst[:n]])


def _compute_sector_rows(ctx: Dict[str, Any]) -> Tuple[List[dict], List[dict]]:
    """
    Returns two row lists:
      1) metrics_rows: Sector, RVOLmMeanΔ, RVOLmMean, RVOLmNetSum, DirR
      2) contrib_rows: Sector, TopBuy, TopSell, N, BuyN, SellN
    """
    LOCK = ctx["LOCK"]
    IST = ctx["IST"]
    SECTOR_DEFINITIONS = ctx["SECTOR_DEFINITIONS"]
    symbol_to_token = ctx["symbol_to_token"]
    DAILY_STATS = ctx["DAILY_STATS"]
    compute_rfactor_row_for_token = ctx["compute_rfactor_row_for_token"]

    now_ist = datetime.now(IST)
    tf = _time_factor_ist(now_ist)

    metrics_rows: List[dict] = []
    contrib_rows: List[dict] = []

    with LOCK:
        for sector, syms in SECTOR_DEFINITIONS.items():
            buy_list: List[dict] = []
            sell_list: List[dict] = []
            rvolm_vals: List[float] = []
            dirr_vals: List[float] = []

            buy_sum = 0.0
            sell_sum = 0.0
            buy_n = 0
            sell_n = 0

            for sym in syms:
                tok = symbol_to_token.get(sym)
                if not tok:
                    continue

                rr = compute_rfactor_row_for_token(tok)
                if not rr:
                    continue

                st = DAILY_STATS.get(tok) or {}
                avg_vol_20 = st.get("avg_vol_20")
                vol_today = rr.get("vol_today")
                pct_open = rr.get("pct_open")
                dirr = rr.get("dirr")

                if avg_vol_20 is None or vol_today is None:
                    continue
                if float(avg_vol_20) <= 0:
                    continue

                expected = float(avg_vol_20) * float(tf)
                rvolm = float(vol_today) / (expected + 1e-9)

                rvolm_vals.append(rvolm)
                if dirr is not None:
                    dirr_vals.append(float(dirr))

                side_row = {"Symbol": sym, "RVOLm": rvolm}

                # BUY/SELL proxy by sign of pct_open
                if pct_open is not None and float(pct_open) >= 0:
                    buy_list.append(side_row)
                    buy_sum += rvolm
                    buy_n += 1
                else:
                    sell_list.append(side_row)
                    sell_sum += rvolm
                    sell_n += 1

            buy_list.sort(key=lambda r: r["RVOLm"], reverse=True)
            sell_list.sort(key=lambda r: r["RVOLm"], reverse=True)

            n_total = len(rvolm_vals)
            rvolm_mean = float(sum(rvolm_vals) / n_total) if n_total > 0 else 0.0

            # Option A: show "below normal" as negative by plotting delta vs 1.0
            rvolm_mean_delta = float(rvolm_mean - 1.0)

            dirr_mean = float(pd.Series(dirr_vals).mean()) if dirr_vals else 0.0
            net_sum = float(buy_sum - sell_sum)

            metrics_rows.append(
                {
                    "Sector": sector,
                    "RVOLmMeanΔ": round(rvolm_mean_delta, 2),
                    "RVOLmMean": round(rvolm_mean, 2),
                    "RVOLmNetSum": round(net_sum, 2),
                    "DirR": round(dirr_mean, 2),
                }
            )

            contrib_rows.append(
                {
                    "Sector": sector,
                    "TopBuy": _fmt_top(buy_list, TOP_N),
                    "TopSell": _fmt_top(sell_list, TOP_N),
                    "N": int(n_total),
                    "BuyN": int(buy_n),
                    "SellN": int(sell_n),
                }
            )

    # Keep both tables in same sector order: sort by RVOLmMeanΔ desc
    metrics_rows.sort(key=lambda r: float(r.get("RVOLmMeanΔ", 0.0)), reverse=True)
    order = {r["Sector"]: i for i, r in enumerate(metrics_rows)}
    contrib_rows.sort(key=lambda r: order.get(r["Sector"], 10**9))

    return metrics_rows, contrib_rows


def contribution_page(BASE: str):
    # ---- TABLE 1: Activity / Direction (no scroll; autoHeight) ----
    metrics_cols = [
        {"colId": "sector", "field": "Sector", "headerName": "SECTOR", "minWidth": 160, "flex": 1, "cellClass": "prem-sector"},
        {
            "colId": "meanDelta",
            "field": "RVOLmMeanΔ",
            "headerName": "RVOLmMean Δ",
            "minWidth": 170,
            "type": "rightAligned",
            "cellClass": "prem-num prem-signed",
            "valueFormatter": {"function": "fmtSigned2(params.value)"},
            "cellClassRules": {"cell-pos": "params.value > 0", "cell-neg": "params.value < 0"},
        },
        {
            "colId": "mean",
            "field": "RVOLmMean",
            "headerName": "RVOLm Mean",
            "minWidth": 150,
            "type": "rightAligned",
            "cellClass": "prem-num",
            "valueFormatter": {"function": "fmt2(params.value)"},
        },
        {
            "colId": "netSum",
            "field": "RVOLmNetSum",
            "headerName": "RVOLm Net SUM",
            "minWidth": 180,
            "type": "rightAligned",
            "cellClass": "prem-num prem-signed",
            "valueFormatter": {"function": "fmtSigned2(params.value)"},
            "cellClassRules": {"cell-pos": "params.value > 0", "cell-neg": "params.value < 0"},
        },
        {
            "colId": "dirr",
            "field": "DirR",
            "headerName": "DirR",
            "minWidth": 120,
            "type": "rightAligned",
            "cellClass": "prem-num prem-signed",
            "valueFormatter": {"function": "fmtSigned2(params.value)"},
            "cellClassRules": {"cell-pos": "params.value > 0", "cell-neg": "params.value < 0"},
        },
    ]

    # ---- TABLE 2: Top Contributors ----
    contrib_cols = [
        {"colId": "sector", "field": "Sector", "headerName": "SECTOR", "minWidth": 160, "flex": 1, "cellClass": "prem-sector"},
        {"colId": "topBuy", "field": "TopBuy", "headerName": "TOP BUY (by RVOLm)", "minWidth": 360, "flex": 2, "cellClass": "prem-text"},
        {"colId": "topSell", "field": "TopSell", "headerName": "TOP SELL (by RVOLm)", "minWidth": 360, "flex": 2, "cellClass": "prem-text"},
        {"colId": "n", "field": "N", "headerName": "N", "minWidth": 80, "type": "rightAligned", "cellClass": "prem-num"},
        {"colId": "buyN", "field": "BuyN", "headerName": "BUY N", "minWidth": 100, "type": "rightAligned", "cellClass": "prem-num"},
        {"colId": "sellN", "field": "SellN", "headerName": "SELL N", "minWidth": 110, "type": "rightAligned", "cellClass": "prem-num"},
    ]

    base_grid_opts = {
        "immutableData": True,
        "animateRows": False,
        "alwaysShowVerticalScroll": False,
        "onGridReady": {"function": "params.api.sizeColumnsToFit();"},
        "onGridSizeChanged": {"function": "params.api.sizeColumnsToFit();"},
    }

    # Metrics grid: show ALL rows (no scroll)
    metrics_grid_opts = dict(base_grid_opts)
    metrics_grid_opts.update(
        {
            "domLayout": "autoHeight",
            "getRowId": {"function": "params.data.Sector"},
            "sortModel": [{"colId": "meanDelta", "sort": "desc"}],
        }
    )

    # Contributors grid: normal fixed height
    contrib_grid_opts = dict(base_grid_opts)
    contrib_grid_opts.update({"getRowId": {"function": "params.data.Sector"}})

    return html.Div(
        [
            dcc.Interval(id="refresh_contrib", interval=2000, n_intervals=0),

            dbc.Row(
                [
                    dbc.Col(html.H4("Sector Contributions", className="page-title premium-title"), width=True),
                    dbc.Col(
                        dbc.Button("Back", href=f"{BASE}", color="secondary", outline=True, className="btn-back"),
                        width="auto",
                    ),
                ],
                className="align-items-center g-2",
            ),

            html.Div(
                "RVOLmMean Δ = mean(RVOLm) − 1 (negative = below normal activity). "
                "RVOLm Net SUM = Σbuy(RVOLm) − Σsell(RVOLm). DirR = mean directional rfactor.",
                className="hint premium-hint",
            ),

            html.Div(
                [
                    html.Div("Sector Activity / Direction", className="premium-section-title"),
                    dag.AgGrid(
                        id="contrib-metrics-grid",
                        className="ag-theme-alpine-dark grid-wrap compact-grid premium-grid",
                        columnDefs=metrics_cols,
                        rowData=[],
                        defaultColDef={"sortable": True, "filter": True, "resizable": True},
                        dashGridOptions=metrics_grid_opts,
                        style={"height": "auto", "width": "100%"},
                    ),
                ],
                className="premium-panel",
            ),

            html.Div(style={"height": "14px"}),

            html.Div(
                [
                    html.Div("Top Contributors (BUY / SELL) + Breadth", className="premium-section-title"),
                    dag.AgGrid(
                        id="contrib-top-grid",
                        className="ag-theme-alpine-dark grid-wrap compact-grid premium-grid",
                        columnDefs=contrib_cols,
                        rowData=[],
                        defaultColDef={"sortable": True, "filter": True, "resizable": True},
                        dashGridOptions=contrib_grid_opts,
                        style={"height": "min(520px, 52vh)", "width": "100%"},
                    ),
                ],
                className="premium-panel",
            ),
        ],
        className="page-wrap",
    )


def register_contribution(dash_app, BASE: str, ctx: Dict[str, Any]) -> None:
    @dash_app.callback(
        Output("contrib-metrics-grid", "rowData"),
        Output("contrib-top-grid", "rowData"),
        Input("refresh_contrib", "n_intervals"),
        prevent_initial_call=False,
    )
    def _update(_n):
        try:
            return _compute_sector_rows(ctx)
        except Exception:
            return [], []