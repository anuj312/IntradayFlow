# rfactor.py
# Dash page plugin: /dash/rfactor
#
# What it shows:
#   1) Sector Rotation (since open -> now)
#        - SectorRet  = mean(%Chg from OPEN) of sector constituents
#        - MarketRet  = mean(%Chg from OPEN) of ALL_SYMBOLS (your universe baseline)
#        - RS         = SectorRet - MarketRet
#        - Mom(20m)   = RS_now - RS_20m_ago (blank until history exists)
#        - Mom(vsPrev)= RS_now - RS_prev_session (prev session baseline from EOD_SNAPSHOT)
#        - State      = quadrant using Mom(20m) if available else Mom(vsPrev)
#        - Flow proxy = ΣRVOLm(buy) - ΣRVOLm(sell), where buy/sell by sign of %Chg(O)
#
#   2) Stocks (paced RFactor)
#        - Top 15 / Bottom 15 by DirR
#        - Build-up UP/DOWN since open (high RVOLm + low |%Chg(O)|)
#        - Hide build-up from 09:15–09:20 IST
#        - ΔR(60s) acceleration column (rolling)
#
# Exposes:
#   rfactor_page(BASE) -> layout
#   register_rfactor(dash_app, BASE, ctx) -> callbacks
#
# ctx MUST include:
#   LOCK, IST, ALL_SYMBOLS, SECTOR_DEFINITIONS, symbol_to_token,
#   compute_rfactor_row_for_token_paced,
#   EOD_SNAPSHOT

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, time as dtime, date as ddate
from typing import Dict, Any, List, Tuple, Optional
from zoneinfo import ZoneInfo

from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc
import dash_ag_grid as dag

log = logging.getLogger("turbotrades.rfactor")

# =============================================================================
# TUNABLES
# =============================================================================
TOP_N = 15

# Build-up (since open)
BUILD_RVOLM_MIN = 2.0
BUILD_ABS_PCTOPEN_MAX = 0.50

# Ignore first 5 minutes after open (Option A)
BUILDUP_START_TIME_IST = dtime(9, 20)

# ΔR acceleration
ACCEL_WINDOW_SEC = 60
HIST_KEEP_SEC = 5 * 60

# Sector momentum lookback
MOM_LOOKBACK_SEC = 20 * 60
SECTOR_HIST_KEEP_SEC = 8 * 60 * 60

IST_FALLBACK = ZoneInfo("Asia/Kolkata")

# =============================================================================
# IN-MEMORY HISTORY
# =============================================================================
# token -> deque[(epoch, rfactor)]
_RHIST: Dict[int, deque] = {}

# sector -> deque[(epoch, RS)]
_SECTOR_HIST: Dict[str, deque] = {}

# prev-session RS baseline cache
_PREV_RS_BASELINE: Dict[str, float] = {}
_PREV_RS_BASELINE_DAY: Optional[ddate] = None


# =============================================================================
# UTIL: history helpers
# =============================================================================
def _push_hist(token: int, epoch: float, rfactor_val: float) -> None:
    dq = _RHIST.get(token)
    if dq is None:
        dq = deque()
        _RHIST[token] = dq

    dq.append((float(epoch), float(rfactor_val)))

    cutoff = float(epoch) - float(HIST_KEEP_SEC)
    while dq and dq[0][0] < cutoff:
        dq.popleft()


def _delta_over_window(token: int, epoch: float, window_sec: float) -> Optional[float]:
    dq = _RHIST.get(token)
    if not dq or len(dq) < 2:
        return None

    cutoff = float(epoch) - float(window_sec)
    base = None
    for t, v in dq:
        if t <= cutoff:
            base = (t, v)
        else:
            break

    if base is None:
        return None

    return float(dq[-1][1] - base[1])


def _push_sector_hist(sector: str, epoch: float, rs: float) -> None:
    key = str(sector).upper()
    dq = _SECTOR_HIST.get(key)
    if dq is None:
        dq = deque()
        _SECTOR_HIST[key] = dq

    dq.append((float(epoch), float(rs)))

    cutoff = float(epoch) - float(SECTOR_HIST_KEEP_SEC)
    while dq and dq[0][0] < cutoff:
        dq.popleft()


def _sector_mom_lookback(sector: str, epoch: float, lookback_sec: float) -> Optional[float]:
    dq = _SECTOR_HIST.get(str(sector).upper())
    if not dq or len(dq) < 2:
        return None

    cutoff = float(epoch) - float(lookback_sec)
    base = None
    for t, v in dq:
        if t <= cutoff:
            base = (t, v)
        else:
            break

    if base is None:
        return None

    return float(dq[-1][1] - base[1])


# =============================================================================
# UTIL: labels
# =============================================================================
def _rotation_state(rs: float, mom: float) -> str:
    if rs >= 0 and mom >= 0:
        return "LEADING"
    if rs >= 0 and mom < 0:
        return "WEAKENING"
    if rs < 0 and mom >= 0:
        return "IMPROVING"
    return "LAGGING"


# =============================================================================
# PREV SESSION BASELINE (for Mom(vsPrev))
# =============================================================================
def _maybe_build_prev_rs_baseline(ctx: Dict[str, Any]) -> Dict[str, float]:
    """
    Builds prev-session RS baseline per sector using EOD_SNAPSHOT open->close returns.
    Returns cached dict: sector -> RS_prev
    """
    global _PREV_RS_BASELINE, _PREV_RS_BASELINE_DAY

    ist = ctx.get("IST") or IST_FALLBACK
    today = datetime.now(ist).date()

    if _PREV_RS_BASELINE and _PREV_RS_BASELINE_DAY == today:
        return _PREV_RS_BASELINE

    _PREV_RS_BASELINE = {}
    _PREV_RS_BASELINE_DAY = today

    eod_snap = ctx.get("EOD_SNAPSHOT") or {}
    if not eod_snap:
        return _PREV_RS_BASELINE

    all_syms: List[str] = ctx["ALL_SYMBOLS"]
    sector_defs: Dict[str, List[str]] = ctx["SECTOR_DEFINITIONS"]
    sym_to_tok: Dict[str, int] = ctx["symbol_to_token"]

    yret: Dict[str, float] = {}
    mkt_vals: List[float] = []

    for sym in all_syms:
        tok = sym_to_tok.get(sym)
        if not tok:
            continue
        e = eod_snap.get(tok)
        if not e:
            continue
        op = e.get("open")
        cl = e.get("close")
        if op is None or cl is None:
            continue
        opf = float(op)
        clf = float(cl)
        if opf <= 0:
            continue
        r = (clf - opf) / opf * 100.0
        yret[sym] = float(r)
        mkt_vals.append(float(r))

    if not mkt_vals:
        return _PREV_RS_BASELINE

    mkt_ret_y = float(sum(mkt_vals) / len(mkt_vals))

    for sector, syms in sector_defs.items():
        vals = [yret[s] for s in syms if s in yret]
        if not vals:
            continue
        sec_ret_y = float(sum(vals) / len(vals))
        _PREV_RS_BASELINE[str(sector)] = float(sec_ret_y - mkt_ret_y)

    return _PREV_RS_BASELINE


# =============================================================================
# STOCK ROWS (paced)
# =============================================================================
def _build_stock_rows(ctx: Dict[str, Any]) -> List[dict]:
    all_syms: List[str] = ctx["ALL_SYMBOLS"]
    sym_to_tok: Dict[str, int] = ctx["symbol_to_token"]
    compute_paced = ctx["compute_rfactor_row_for_token_paced"]

    now = time.time()

    rows: List[dict] = []
    for sym in all_syms:
        tok = sym_to_tok.get(sym)
        if not tok:
            continue

        rr = compute_paced(tok)
        if not rr:
            continue

        rfac = float(rr["rfactor"])
        _push_hist(tok, now, rfac)
        dR = _delta_over_window(tok, now, ACCEL_WINDOW_SEC)

        pct_open = float(rr["pct_open"])
        rvolm = rr.get("rvolm")
        rvolm_f = float(rvolm) if rvolm is not None else None

        build_score = None
        if rvolm_f is not None:
            build_score = rvolm_f / (abs(pct_open) + 0.10)

        rows.append(
            {
                "Symbol": sym,
                "%Chg(O)": round(pct_open, 2),
                "RVOLm": (round(rvolm_f, 2) if rvolm_f is not None else None),
                "RFactor": round(rfac, 2),
                "DirR": round(float(rr["dirr"]), 2),
                "ΔR60s": (round(float(dR), 2) if dR is not None else None),
                "BuildScore": (round(float(build_score), 2) if build_score is not None else None),
                "Vol": int(rr["vol_today"]),
            }
        )

    return rows


def _top_bottom_dirr(rows: List[dict], n: int = TOP_N) -> Tuple[List[dict], List[dict]]:
    rows2 = [r for r in rows if r.get("DirR") is not None]
    top = sorted(rows2, key=lambda r: float(r["DirR"]), reverse=True)[:n]
    bottom = sorted(rows2, key=lambda r: float(r["DirR"]))[:n]
    return top, bottom


def _build_up_down_since_open(rows: List[dict], n: int = TOP_N) -> Tuple[List[dict], List[dict]]:
    filt = []
    for r in rows:
        rvolm = r.get("RVOLm")
        pct = r.get("%Chg(O)")
        if rvolm is None or pct is None:
            continue
        if float(rvolm) < BUILD_RVOLM_MIN:
            continue
        if abs(float(pct)) > BUILD_ABS_PCTOPEN_MAX:
            continue
        filt.append(r)

    up = [r for r in filt if float(r["%Chg(O)"]) >= 0]
    dn = [r for r in filt if float(r["%Chg(O)"]) < 0]

    def key(r):
        return (float(r.get("BuildScore") or 0.0), float(r.get("ΔR60s") or 0.0))

    up = sorted(up, key=key, reverse=True)[:n]
    dn = sorted(dn, key=key, reverse=True)[:n]
    return up, dn


# =============================================================================
# SECTOR ROTATION
# =============================================================================
def _compute_sector_rotation(ctx: Dict[str, Any], stock_rows: List[dict]) -> List[dict]:
    sector_defs: Dict[str, List[str]] = ctx["SECTOR_DEFINITIONS"]
    prev_rs = _maybe_build_prev_rs_baseline(ctx)

    market_vals = [float(r["%Chg(O)"]) for r in stock_rows if r.get("%Chg(O)") is not None]
    market_ret = float(sum(market_vals) / len(market_vals)) if market_vals else 0.0

    by_sym = {r["Symbol"]: r for r in stock_rows if r.get("%Chg(O)") is not None}

    now = time.time()
    out: List[dict] = []

    for sector, syms in sector_defs.items():
        vals: List[float] = []
        dirr_vals: List[float] = []

        buy_sum = sell_sum = 0.0
        buy_n = sell_n = 0

        for sym in syms:
            r = by_sym.get(sym)
            if not r:
                continue

            pct = r.get("%Chg(O)")
            rvolm = r.get("RVOLm")
            dirr = r.get("DirR")

            if pct is None:
                continue

            vals.append(float(pct))

            if dirr is not None:
                dirr_vals.append(float(dirr))

            if rvolm is not None:
                if float(pct) >= 0:
                    buy_sum += float(rvolm)
                    buy_n += 1
                else:
                    sell_sum += float(rvolm)
                    sell_n += 1

        if not vals:
            continue

        n = len(vals)
        sector_ret = float(sum(vals) / n)
        rs = float(sector_ret - market_ret)

        _push_sector_hist(sector, now, rs)

        mom20 = _sector_mom_lookback(sector, now, MOM_LOOKBACK_SEC)  # None until enough history exists
        mom_prev = None
        if str(sector) in prev_rs:
            mom_prev = float(rs - float(prev_rs[str(sector)]))

        mom_for_state = float(mom20) if mom20 is not None else (float(mom_prev) if mom_prev is not None else 0.0)
        state = _rotation_state(rs, mom_for_state)

        flow_net = float(buy_sum - sell_sum)
        flow_gross_mean = float((buy_sum + sell_sum) / n) if n > 0 else 0.0
        buy_share = float(buy_sum / (buy_sum + sell_sum + 1e-9))
        dirr_mean = float(sum(dirr_vals) / len(dirr_vals)) if dirr_vals else 0.0

        out.append(
            {
                "Sector": str(sector),
                "State": state,
                "SectorRet": round(sector_ret, 2),
                "MarketRet": round(market_ret, 2),
                "RS": round(rs, 2),
                "Mom20m": (round(float(mom20), 2) if mom20 is not None else None),
                "MomPrev": (round(float(mom_prev), 2) if mom_prev is not None else None),
                "FlowNet": round(flow_net, 2),
                "FlowGrossMean": round(flow_gross_mean, 2),
                "BuyShare": round(buy_share, 2),
                "DirR": round(dirr_mean, 2),
                "N": int(n),
                "BuyN": int(buy_n),
                "SellN": int(sell_n),
            }
        )

    # sort by RS, then Mom20m (or MomPrev fallback)
    def _sort_key(r: dict):
        m = r.get("Mom20m")
        if m is None:
            m = r.get("MomPrev") or 0.0
        return (float(r.get("RS") or 0.0), float(m))

    out.sort(key=_sort_key, reverse=True)
    return out


# =============================================================================
# PAGE LAYOUT
# =============================================================================
def rfactor_page(BASE: str):
    # Signed rules for premium CSS pills
    pill_rules = {
        "sr-pos": "params.value > 0",
        "sr-neg": "params.value < 0",
        "sr-zero": "params.value === 0",
    }

    # Safe JS formatters (won't break if fmtSigned2/fmt2 are missing)
    fmt_signed2_safe = (
        "params.value==null ? '—' : "
        "(typeof fmtSigned2==='function' ? fmtSigned2(params.value) : "
        "((params.value>0?'+':'') + Number(params.value).toFixed(2)))"
    )
    fmt_2_safe = (
        "params.value==null ? '—' : "
        "(typeof fmt2==='function' ? fmt2(params.value) : Number(params.value).toFixed(2))"
    )

    cols_sector = [
        {"colId": "sector", "field": "Sector", "headerName": "SECTOR", "minWidth": 140, "flex": 1, "cellClass": "sr-sector"},
        {"colId": "state", "field": "State", "headerName": "STATE", "minWidth": 140, "cellClass": "sr-state"},

        {"colId": "sectorRet", "field": "SectorRet", "headerName": "SECTOR %", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe},
         "cellClass": "sr-pill sr-num", "cellClassRules": pill_rules, "minWidth": 110},

        {"colId": "marketRet", "field": "MarketRet", "headerName": "MKT %", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe},
         "cellClass": "sr-pill sr-num", "cellClassRules": pill_rules, "minWidth": 95},

        {"colId": "rs", "field": "RS", "headerName": "RS", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe},
         "cellClass": "sr-pill sr-num sr-rs", "cellClassRules": pill_rules, "minWidth": 90},

        {"colId": "mom20", "field": "Mom20m", "headerName": "MOM (20m)", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe},
         "cellClass": "sr-pill sr-num sr-mom", "cellClassRules": pill_rules, "minWidth": 120},

        {"colId": "momPrev", "field": "MomPrev", "headerName": "MOM (vsPrev)", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe},
         "cellClass": "sr-pill sr-num sr-mom", "cellClassRules": pill_rules, "minWidth": 140},

        {"colId": "flowNet", "field": "FlowNet", "headerName": "FLOW NET", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe},
         "cellClass": "sr-pill sr-num sr-flow", "cellClassRules": pill_rules, "minWidth": 120},

        {"colId": "flowGrossMean", "field": "FlowGrossMean", "headerName": "FLOW GROSS μ", "type": "rightAligned",
         "valueFormatter": {"function": fmt_2_safe},
         "cellClass": "sr-pill sr-num sr-gross", "minWidth": 140},

        {"colId": "buyShare", "field": "BuyShare", "headerName": "BUY SHARE", "type": "rightAligned",
         "valueFormatter": {"function": "params.value==null ? '—' : (Number(params.value)*100).toFixed(0)+'%'" },
         "cellClass": "sr-pill sr-num sr-share", "minWidth": 120},

        {"colId": "dirr", "field": "DirR", "headerName": "DIR R", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe},
         "cellClass": "sr-pill sr-num sr-dirr", "cellClassRules": pill_rules, "minWidth": 95},

        {"colId": "n", "field": "N", "headerName": "N", "type": "rightAligned",
         "valueFormatter": {"function": "params.value==null ? '—' : String(params.value)"},
         "cellClass": "sr-pill sr-num sr-n", "minWidth": 70},

        {"colId": "buyN", "field": "BuyN", "headerName": "BUY N", "type": "rightAligned",
         "valueFormatter": {"function": "params.value==null ? '—' : String(params.value)"},
         "cellClass": "sr-pill sr-num sr-n", "minWidth": 90},

        {"colId": "sellN", "field": "SellN", "headerName": "SELL N", "type": "rightAligned",
         "valueFormatter": {"function": "params.value==null ? '—' : String(params.value)"},
         "cellClass": "sr-pill sr-num sr-n", "minWidth": 95},
    ]

    cols_main = [
        {"colId": "stock", "field": "Symbol", "headerName": "STOCK", "cellRenderer": "SymbolCell", "minWidth": 120, "flex": 1},
        {"colId": "pct", "field": "%Chg(O)", "headerName": "%CHG(O)", "cellRenderer": "PctPill", "minWidth": 140, "maxWidth": 160},
        {"colId": "rvolm", "field": "RVOLm", "headerName": "RVOLm", "type": "rightAligned",
         "valueFormatter": {"function": fmt_2_safe}, "minWidth": 110, "maxWidth": 130},
        {"colId": "rf", "field": "RFactor", "headerName": "RFACTOR", "cellRenderer": "RfactorPill", "minWidth": 125, "maxWidth": 170},
        {"colId": "dirr", "field": "DirR", "headerName": "DIR R", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe}, "minWidth": 110, "maxWidth": 130},
        {"colId": "dr", "field": "ΔR60s", "headerName": "ΔR(60s)", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe}, "minWidth": 120, "maxWidth": 140},
        {"colId": "vol", "field": "Vol", "headerName": "VOLUME", "cellRenderer": "VolPill", "minWidth": 140, "maxWidth": 190},
    ]

    cols_build = [
        {"colId": "stock", "field": "Symbol", "headerName": "STOCK", "cellRenderer": "SymbolCell", "minWidth": 120, "flex": 1},
        {"colId": "score", "field": "BuildScore", "headerName": "BUILD SCORE", "type": "rightAligned",
         "valueFormatter": {"function": fmt_2_safe}, "minWidth": 140, "maxWidth": 160},
        {"colId": "rvolm", "field": "RVOLm", "headerName": "RVOLm", "type": "rightAligned",
         "valueFormatter": {"function": fmt_2_safe}, "minWidth": 110, "maxWidth": 130},
        {"colId": "pct", "field": "%Chg(O)", "headerName": "%CHG(O)", "cellRenderer": "PctPill", "minWidth": 140, "maxWidth": 160},
        {"colId": "dr", "field": "ΔR60s", "headerName": "ΔR(60s)", "type": "rightAligned",
         "valueFormatter": {"function": fmt_signed2_safe}, "minWidth": 120, "maxWidth": 140},
        {"colId": "rf", "field": "RFactor", "headerName": "RFACTOR", "cellRenderer": "RfactorPill", "minWidth": 125, "maxWidth": 170},
    ]

    base_grid_opts = {
        "alwaysShowVerticalScroll": False,
        "animateRows": False,
        "suppressMenuHide": False,
        "onGridReady": {"function": "params.api.sizeColumnsToFit();"},
        "onGridSizeChanged": {"function": "params.api.sizeColumnsToFit();"},
    }

    sector_grid_opts = dict(base_grid_opts)
    sector_grid_opts["getRowId"] = {"function": "params.data.Sector"}
    sector_grid_opts["domLayout"] = "autoHeight"

    return html.Div(
        [
            dcc.Interval(id="refresh_rfactor", interval=2000, n_intervals=0),

            dbc.Row(
                [
                    dbc.Col(html.H4("RFactor (PACED) • Sector Rotation • Build-up", className="page-title"), width=True),
                    dbc.Col(dbc.Button("Back", href=f"{BASE}", color="secondary", outline=True, className="btn-back"), width="auto"),
                ],
                className="align-items-center g-2",
            ),

            html.Div(
                "RS = SectorRet − MarketRet (since open). "
                "Mom(20m) = ΔRS over last 20m (blank until available). "
                "Mom(vsPrev) = RS − prev-session RS baseline. "
                f"Build-up hidden until {BUILDUP_START_TIME_IST.strftime('%H:%M')} IST.",
                className="hint",
            ),

            html.Hr(),

            html.H6("Sector Rotation", className="mt-1"),
            dag.AgGrid(
                id="sector-rotation-grid",
                className="ag-theme-alpine-dark grid-wrap compact-grid sr-premium-grid",
                columnDefs=cols_sector,
                rowData=[],
                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                dashGridOptions=sector_grid_opts,
                style={"height": "auto", "width": "100%"},
            ),

            html.Hr(),

            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.H6("Top 15 (DirR strongest +)", className="mt-1"),
                            dag.AgGrid(
                                id="rf-top-grid",
                                className="ag-theme-alpine-dark grid-wrap compact-grid",
                                columnDefs=cols_main,
                                rowData=[],
                                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                                dashGridOptions=base_grid_opts,
                                style={"height": "min(520px, 45vh)", "width": "100%"},
                            ),
                        ],
                        md=6,
                    ),
                    dbc.Col(
                        [
                            html.H6("Bottom 15 (DirR strongest −)", className="mt-1"),
                            dag.AgGrid(
                                id="rf-bottom-grid",
                                className="ag-theme-alpine-dark grid-wrap compact-grid",
                                columnDefs=cols_main,
                                rowData=[],
                                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                                dashGridOptions=base_grid_opts,
                                style={"height": "min(520px, 45vh)", "width": "100%"},
                            ),
                        ],
                        md=6,
                    ),
                ],
                className="g-2",
            ),

            html.Hr(),

            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.H6("Build-up UP (since open)", className="mt-1"),
                            dag.AgGrid(
                                id="build-up-grid",
                                className="ag-theme-alpine-dark grid-wrap compact-grid",
                                columnDefs=cols_build,
                                rowData=[],
                                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                                dashGridOptions=base_grid_opts,
                                style={"height": "min(520px, 40vh)", "width": "100%"},
                            ),
                        ],
                        md=6,
                    ),
                    dbc.Col(
                        [
                            html.H6("Build-up DOWN (since open)", className="mt-1"),
                            dag.AgGrid(
                                id="build-down-grid",
                                className="ag-theme-alpine-dark grid-wrap compact-grid",
                                columnDefs=cols_build,
                                rowData=[],
                                defaultColDef={"sortable": True, "filter": True, "resizable": True},
                                dashGridOptions=base_grid_opts,
                                style={"height": "min(520px, 40vh)", "width": "100%"},
                            ),
                        ],
                        md=6,
                    ),
                ],
                className="g-2",
            ),
        ],
        className="page-wrap",
    )


# =============================================================================
# REGISTER CALLBACKS
# =============================================================================
def register_rfactor(dash_app, BASE: str, ctx: Dict[str, Any]) -> None:
    @dash_app.callback(
        Output("sector-rotation-grid", "rowData"),
        Output("rf-top-grid", "rowData"),
        Output("rf-bottom-grid", "rowData"),
        Output("build-up-grid", "rowData"),
        Output("build-down-grid", "rowData"),
        Input("refresh_rfactor", "n_intervals"),
        prevent_initial_call=False,
    )
    def _update(_n):
        try:
            with ctx["LOCK"]:
                stock_rows = _build_stock_rows(ctx)

            sector_rows = _compute_sector_rotation(ctx, stock_rows)
            top, bottom = _top_bottom_dirr(stock_rows, n=TOP_N)

            ist = ctx.get("IST") or IST_FALLBACK
            now_ist = datetime.now(ist).time()

            if now_ist < BUILDUP_START_TIME_IST:
                bup, bdn = [], []
            else:
                bup, bdn = _build_up_down_since_open(stock_rows, n=TOP_N)

            return sector_rows, top, bottom, bup, bdn

        except Exception:
            log.exception("rfactor page update crashed")
            return [], [], [], [], []