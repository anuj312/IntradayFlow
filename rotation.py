# rotation.py
#
# SECTOR ROTATION MODULE (Robust Version)
# Mounted at /rotation/ in the main app
#

import os
import time
import threading
from collections import deque
from typing import Dict, List, Optional
from urllib.parse import quote as urlquote

import pandas as pd
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from kiteconnect import KiteConnect

# =============================================================================
# CONFIG
# =============================================================================
BASE_PATH = "/rotation" # Must match mount path

API_KEY = os.getenv("KITE_API_KEY", "").strip()
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "").strip()

REFRESH_SEC = 2.0
DELTA_WINDOW_SEC = 300   # 5 minutes
HISTORY_KEEP_SEC = 7200  # 2 hours
QUOTE_TTL_SEC = 1.0      # Cache duration
VOL_SPIKE_MULT = 1.50
VOL_MIN_5M = 1000

TRADINGVIEW_BASE = os.getenv("TRADINGVIEW_BASE", "https://www.tradingview.com/chart/")
TRADINGVIEW_INTERVAL = "5"

# =============================================================================
# KITE INIT
# =============================================================================
kite = None
try:
    if API_KEY and ACCESS_TOKEN:
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(ACCESS_TOKEN)
        print("Rotation Module: Kite Client Initialized")
    else:
        print("Rotation Module: Keys missing")
except Exception as e:
    print(f"Rotation Module: Kite init failed: {e}")

app = FastAPI(title="Sector Rotation", docs_url=None, redoc_url=None)

# =============================================================================
# DEFINITIONS
# =============================================================================
SECTOR_DEFINITIONS = {
    "METAL": ["ADANIENT","HINDALCO","JSWSTEEL","HINDZINC","APLAPOLLO","TATASTEEL","JINDALSTEL","VEDL","SAIL","NATIONALUM","NMDC"],
    "PSUS": ["BANKINDIA","PNB","INDIANB","SBIN","UNIONBANK","BANKBARODA","CANBK"],
    "REALTY": ["PHOENIXLTD","GODREJPROP","LODHA","OBEROIRLTY","DLF","PRESTIGE","NBCC","NCC"],
    "ENERGY": ["CGPOWER","RELIANCE","GMRAIRPORT","JSWENERGY","ONGC","POWERGRID","BLUESTARCO","COALINDIA","SUZLON","IREDA","IOC","IGL","TATAPOWER","NTPC","BPCL","ADANIGREEN"],
    "AUTO": ["BOSCHLTD","HEROMOTOCO","M&M","EICHERMOT","BAJAJ-AUTO","MARUTI","TVSMOTOR","MOTHERSON","TATAMOTORS","BHARATFORG"],
    "IT": ["LTIM","TCS","TECHM","HCLTECH","WIPRO","COFORGE","PERSISTENT","INFY","LTTS","MPHASIS"],
    "PHARMA": ["CIPLA","ALKEM","DRREDDY","DIVISLAB","LUPIN","AUROPHARMA","SUNPHARMA","TORNTPHARM"],
    "FMCG": ["MARICO","NESTLEIND","VBL","COLPAL","HINDUNILVR","DMART","DABUR","GODREJCP","BRITANNIA","ITC","TATACONSUM"],
    "CEMENT": ["SHREECEM","DALBHARAT","AMBUJACEM","ULTRACEMCO","GRASIM"],
    "FINSERVICE": ["BAJAJFINSV","HDFCLIFE","RECLTD","BAJFINANCE","PFC","HDFCAMC","SBILIFE","SHRIRAMFIN","CHOLAFIN"],
    "BANK": ["IDFCFIRSTB","INDUSINDBK","HDFCBANK","SBIN","KOTAKBANK","AUBANK","AXISBANK","ICICIBANK","BANKBARODA","PNB"],
    "NIFTY_50": ["RELIANCE","HDFCBANK","INFY","ICICIBANK","TCS","ITC","LT","AXISBANK","SBIN","BHARTIARTL"],
    "MIDCAP": ["TRENT","BEL","HAL","VBL","TATAELXSI","ABB","PAGEIND","PIIND","ASTRAL","POLYCAB"]
}

# IMPORTANT: Correct Index Symbols for Kite
INDEX_CANDIDATES = {
    "NIFTY_50": ["NSE:NIFTY 50", "NSE:NIFTY50"],
    "BANK": ["NSE:NIFTY BANK", "NSE:BANKNIFTY"],
    "FINSERVICE": ["NSE:NIFTY FIN SERVICE"],
    "IT": ["NSE:NIFTY IT"],
    "AUTO": ["NSE:NIFTY AUTO"],
    "PHARMA": ["NSE:NIFTY PHARMA"],
    "FMCG": ["NSE:NIFTY FMCG"],
    "METAL": ["NSE:NIFTY METAL"],
    "REALTY": ["NSE:NIFTY REALTY"],
    "PSUS": ["NSE:NIFTY PSU BANK"],
    "ENERGY": ["NSE:NIFTY ENERGY"],
    "CEMENT": ["NSE:NIFTY COMMODITIES"], # Proxy
    "MIDCAP": ["NSE:NIFTY MIDCAP 100"]
}

RESOLVED_INDEX = {}
HISTORY = {}
HLOCK = threading.Lock()
_QCACHE = {}
_QLOCK = threading.Lock()

# =============================================================================
# LOGIC
# =============================================================================
def _quote_cached(keys):
    if not kite: return {}
    keys = list(sorted(set(keys)))
    cache_key = "|".join(keys)
    now = time.time()
    
    with _QLOCK:
        cached = _QCACHE.get(cache_key)
        if cached and cached[1] > now: return cached[0]
    
    try:
        # Batch quote
        q = kite.quote(keys)
        with _QLOCK:
            _QCACHE[cache_key] = (q, now + QUOTE_TTL_SEC)
        return q
    except Exception as e:
        print(f"Quote Error: {e}")
        return {}

def _history_push_and_get_delta(key, value, now):
    with HLOCK:
        dq = HISTORY.setdefault(key, deque())
        dq.append((now, float(value)))
        
        # Cleanup old
        while dq and dq[0][0] < (now - HISTORY_KEEP_SEC): 
            dq.popleft()
            
        # Get 5m delta
        target = now - DELTA_WINDOW_SEC
        prev = None
        # Walk backwards to find closest time <= target
        for t, v in reversed(dq):
            if t <= target:
                prev = v
                break
                
    if prev is not None:
        return float(value) - float(prev)
    return None

def _resolve_indices():
    """Finds valid index symbols."""
    print("Rotation: Resolving indices...")
    res = {}
    for k, candidates in INDEX_CANDIDATES.items():
        for sym in candidates:
            try:
                # Direct fetch to check validity
                q = kite.quote([sym])
                if q and sym in q:
                    res[k] = sym
                    print(f"  -> Resolved {k}: {sym}")
                    break
            except: 
                pass
    return res

def check_indices_loaded():
    """Ensures indices are loaded before processing."""
    global RESOLVED_INDEX
    if not RESOLVED_INDEX:
        RESOLVED_INDEX = _resolve_indices()

@app.on_event("startup")
def startup_event():
    # Try to resolve on startup
    if kite: check_indices_loaded()

def _get_pct(q, sym):
    d = q.get(sym)
    if not d: return None
    op = d.get('ohlc', {}).get('open')
    lp = d.get('last_price')
    if op and lp and op > 0: 
        return (lp - op) / op * 100.0
    return None

# =============================================================================
# DATA PROCESSING
# =============================================================================
def get_index_data():
    check_indices_loaded()
    if not RESOLVED_INDEX or "NIFTY_50" not in RESOLVED_INDEX:
        return pd.DataFrame() # Still empty

    # Fetch all indices at once
    keys = list(RESOLVED_INDEX.values())
    q = _quote_cached(keys)
    
    nifty_p = _get_pct(q, RESOLVED_INDEX["NIFTY_50"])
    if nifty_p is None: nifty_p = 0.0 # Fallback

    rows = []
    now = time.time()
    
    for sec in sorted(SECTOR_DEFINITIONS.keys()):
        sym = RESOLVED_INDEX.get(sec)
        if not sym: continue
        
        sp = _get_pct(q, sym)
        if sp is None: continue
        
        rel = sp - nifty_p
        d5 = _history_push_and_get_delta(f"IDX:{sec}", rel, now)
        
        state = "WARMING UP"
        if d5 is not None:
            if rel > 0 and d5 > 0: state = "ROTATING IN"
            elif rel < 0 and d5 < 0: state = "ROTATING OUT"
            else: state = "NEUTRAL"
            
        rows.append({
            "Sector": sec, 
            "Sec%": sp, 
            "Rel%": rel, 
            "RelΔ5m": d5, 
            "State": state,
            "Score": rel # Use Rel% for sorting mainly
        })
        
    return pd.DataFrame(rows)

def get_stock_data(sector):
    check_indices_loaded()
    syms = SECTOR_DEFINITIONS.get(sector, [])
    idx = RESOLVED_INDEX.get(sector)
    
    keys = [f"NSE:{s}" for s in syms] + ([idx] if idx else [])
    q = _quote_cached(keys)
    
    sec_p = _get_pct(q, idx) if idx else 0.0
    if sec_p is None: sec_p = 0.0
    
    rows = []
    now = time.time()
    
    for s in syms:
        k = f"NSE:{s}"
        d = q.get(k)
        if not d: continue
        
        sp = _get_pct(q, k)
        if sp is None: continue
        
        rel = sp - sec_p
        d5 = _history_push_and_get_delta(f"STK:{s}", rel, now)
        
        # Volume Logic
        vol = d.get('volume', 0)
        v5 = _history_push_and_get_delta(f"VOL:{s}", vol, now)
        
        state = "NEUTRAL"
        if d5 is not None:
            if rel > 0 and d5 > 0: state = "ROTATING IN"
            elif rel < 0 and d5 < 0: state = "ROTATING OUT"
            
        vwap = d.get('average_price', 0)
        ltp = d.get('last_price', 0)
        
        # Signal
        sig = "—"
        if state == "ROTATING IN" and ltp > vwap and (v5 and v5 > VOL_MIN_5M):
            sig = "LONG"
        elif state == "ROTATING OUT" and ltp < vwap and (v5 and v5 > VOL_MIN_5M):
            sig = "SHORT"
            
        rows.append({
            "Symbol": s, "LTP": ltp, "VWAP": vwap,
            "Stock%": sp, "Rel%": rel, "RelΔ5m": d5, "VolΔ5m": v5,
            "State": state, "Signal": sig
        })
        
    return pd.DataFrame(rows)

# =============================================================================
# UI FORMATTING
# =============================================================================
def fmt_pct(v): return f"{v:+.2f}%" if v is not None else "—"
def fmt_num(v): return f"{v:.2f}" if v is not None else "—"
def fmt_int(v): return f"{int(v):,}" if v is not None else "—"

def color_num(v):
    if v is None: return "var(--muted)"
    return "var(--good)" if v > 0 else "var(--bad)"

def color_state(s):
    if "IN" in s: return "var(--good)"
    if "OUT" in s: return "var(--bad)"
    return "var(--muted)"

def color_sig(s):
    if "LONG" in s: return "var(--good)"
    if "SHORT" in s: return "var(--bad)"
    return "var(--muted)"

# =============================================================================
# CSS
# =============================================================================
CSS = """
<style>
/* JEWEL TONE / ONYX THEME */
:root {
  --bg0: #020202; --bg1: #0a0a0a;
  --panel0: rgba(255,255,255,0.06);
  --panel1: rgba(255,255,255,0.03);
  --border: rgba(255,255,255,0.14); --border2: rgba(255,255,255,0.09);
  --text: rgba(255,255,255,0.94); --muted: rgba(255,255,255,0.60);
  /* Fluorescent Jewel Tones */
  --good: #2ed573; /* Emerald */
  --bad: #ff4757;  /* Ruby */
  --mono: ui-monospace, SFMono-Regular, Consolas, monospace;
}
body.light-mode {
  --bg0: #ffffff; --bg1: #f3f4f6;
  --panel0: #ffffff; --panel1: #f9fafb;
  --border: #e5e7eb; --border2: #f3f4f6;
  --text: #111827; --muted: #6b7280;
  --good: #10b981; --bad: #ef4444;
}

body { margin:0; color:var(--text); font-family:system-ui,-apple-system,sans-serif; background:linear-gradient(180deg,var(--bg0),var(--bg1)); padding:16px; min-height:100vh; }
.wrap { max-width:1400px; margin:0 auto; }
a { color:inherit; text-decoration:none; }

/* HEADER */
.head { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; background:var(--panel0); padding:16px; border-radius:16px; border:1px solid var(--border); }
h2 { margin:0; font-size:18px; font-weight:800; letter-spacing:0.5px; }
.btn { padding:6px 14px; border-radius:99px; border:1px solid var(--border); background:var(--panel1); color:var(--text); font-weight:700; cursor:pointer; font-size:12px; }
.btn:hover { filter:brightness(1.2); }

/* GRID */
.mini-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(450px, 1fr)); gap:16px; margin-bottom:20px; }
.card { background:var(--panel0); border:1px solid var(--border); border-radius:16px; overflow:hidden; display:flex; flex-direction:column; }
.card-pad { padding:16px; }
.title { font-size:11px; font-weight:800; text-transform:uppercase; letter-spacing:1px; margin-bottom:12px; color:var(--muted); }
.long { color:var(--good); } .short { color:var(--bad); }

/* TABLES */
.table-wrap { overflow-x:auto; width:100%; }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th, td { padding:10px 12px; border-bottom:1px solid var(--border2); white-space:nowrap; font-size:13px; font-family:var(--mono); }
th { text-align:right; color:var(--muted); font-weight:700; text-transform:uppercase; font-size:11px; background:var(--panel1); }
td { text-align:right; font-weight:600; color:var(--text); }

/* STICKY COLUMN */
th:first-child, td:first-child { 
    position:sticky; left:0; z-index:2; text-align:left; 
    border-right:1px solid var(--border2); background:var(--bg0); 
}
th:first-child { z-index:3; background:var(--panel1); }
tr:hover td { background:var(--panel1); }
tr:hover td:first-child { background:var(--panel1); }

/* FIXED ALIGNMENT FOR MINI TABLES */
table.mini { table-layout:fixed; width:100%; }
table.mini th:first-child { width:40%; } 
table.mini th:nth-child(2) { width:20%; }
table.mini th:nth-child(3) { width:20%; }
table.mini th:nth-child(4) { width:20%; }

</style>
<script>
function toggleTheme() {
    document.body.classList.toggle('light-mode');
    localStorage.setItem('theme', document.body.classList.contains('light-mode') ? 'light' : 'dark');
}
if(localStorage.getItem('theme')==='light') document.body.classList.add('light-mode');
</script>
"""

# =============================================================================
# ROUTES
# =============================================================================
@app.get("/", response_class=HTMLResponse)
def index():
    if not kite:
        return f"<html><body style='background:#111;color:#fff'><h3>Error: API Keys Missing</h3></body></html>"
        
    df = get_index_data()
    
    if df.empty:
        # Show loading or empty state
        mini_l = mini_s = "<tr><td colspan=4 style='text-align:center;padding:20px;color:var(--muted)'>Initializing indices... (Reload in 5s)</td></tr>"
        main_body = "<tr><td colspan=6 style='text-align:center;padding:20px'>Waiting for data...</td></tr>"
    else:
        # Top 5 Logic
        longs = df[df["State"]=="ROTATING IN"].sort_values(["RelΔ5m", "Rel%"], ascending=False).head(5)
        shorts = df[df["State"]=="ROTATING OUT"].sort_values(["RelΔ5m", "Rel%"], ascending=True).head(5)
        
        def _mini_row(r):
            sec = r["Sector"]
            lnk = f"{BASE_PATH}/sector/{sec}"
            return f"<tr><td><a href='{lnk}'><b>{sec}</b></a></td><td style='color:{color_num(r['Sec%'])}'>{fmt_pct(r['Sec%'])}</td><td style='color:{color_num(r['Rel%'])}'>{fmt_pct(r['Rel%'])}</td><td style='color:{color_num(r['RelΔ5m'])}'>{fmt_pct(r['RelΔ5m'])}</td></tr>"

        mini_l = "".join([_mini_row(r) for _, r in longs.iterrows()]) or "<tr><td colspan=4 style='text-align:center;color:var(--muted)'>None</td></tr>"
        mini_s = "".join([_mini_row(r) for _, r in shorts.iterrows()]) or "<tr><td colspan=4 style='text-align:center;color:var(--muted)'>None</td></tr>"
        
        # Main Table
        rows = []
        df = df.sort_values("Rel%", ascending=False)
        for _, r in df.iterrows():
            sec = r["Sector"]
            lnk = f"{BASE_PATH}/sector/{sec}"
            rows.append(f"<tr><td><a href='{lnk}' style='font-weight:700'>{sec}</a></td><td style='color:{color_num(r['Sec%'])}'>{fmt_pct(r['Sec%'])}</td><td style='color:{color_num(r['Rel%'])}'>{fmt_pct(r['Rel%'])}</td><td style='color:{color_num(r['RelΔ5m'])}'>{fmt_pct(r['RelΔ5m'])}</td><td style='color:{color_state(r['State'])}'>{r['State']}</td><td>{fmt_num(r['Score'])}</td></tr>")
        main_body = "".join(rows)

    return f"""
    <!doctype html><html><head><meta charset='utf-8'><title>Sector Rotation</title>{CSS}<meta http-equiv='refresh' content='{REFRESH_SEC}'></head><body>
    <div class='wrap'>
        <div class='head'>
            <div><h2>Sector Rotation</h2><div style='font-size:12px;color:var(--muted)'>Relative Strength vs NIFTY 50</div></div>
            <div><button class='btn' onclick='toggleTheme()'>◑ Theme</button> <span class='btn' style='cursor:default'>Refresh {REFRESH_SEC}s</span></div>
        </div>
        
        <div class='mini-grid'>
            <div class='card card-pad'>
                <div class='title long'>Top 5 Rotating In (Buy)</div>
                <table class='mini'><thead><tr><th>Sector</th><th>Sec%</th><th>Rel%</th><th>Δ5m</th></tr></thead><tbody>{mini_l}</tbody></table>
            </div>
            <div class='card card-pad'>
                <div class='title short'>Top 5 Rotating Out (Sell)</div>
                <table class='mini'><thead><tr><th>Sector</th><th>Sec%</th><th>Rel%</th><th>Δ5m</th></tr></thead><tbody>{mini_s}</tbody></table>
            </div>
        </div>

        <div class='card'>
            <div class='table-wrap'>
                <table><thead><tr><th>Sector</th><th>Sec%</th><th>Rel%</th><th>RelΔ5m</th><th>State</th><th>Score</th></tr></thead><tbody>{main_body}</tbody></table>
            </div>
        </div>
    </div></body></html>
    """

@app.get("/sector/{sec}", response_class=HTMLResponse)
def sector_view(sec: str):
    df = get_stock_data(sec)
    rows = []
    if df.empty:
        body = "<tr><td colspan=9 style='text-align:center;padding:20px'>Loading sector data...</td></tr>"
    else:
        df = df.sort_values("Rel%", ascending=False)
        for _, r in df.iterrows():
            sym = r["Symbol"]
            tv = f"{TRADINGVIEW_BASE}?symbol=NSE:{sym}&interval={TRADINGVIEW_INTERVAL}"
            rows.append(f"<tr><td><a href='{tv}' target='_blank' style='font-weight:700'>{sym}</a></td><td>{fmt_num(r['LTP'])}</td><td>{fmt_num(r['VWAP'])}</td><td style='color:{color_num(r['Stock%'])}'>{fmt_pct(r['Stock%'])}</td><td style='color:{color_num(r['Rel%'])}'>{fmt_pct(r['Rel%'])}</td><td style='color:{color_num(r['RelΔ5m'])}'>{fmt_pct(r['RelΔ5m'])}</td><td>{fmt_int(r['VolΔ5m'])}</td><td style='color:{color_state(r['State'])}'>{r['State']}</td><td style='color:{color_sig(r['Signal'])};font-weight:900'>{r['Signal']}</td></tr>")
        body = "".join(rows)

    return f"""
    <!doctype html><html><head><meta charset='utf-8'><title>{sec}</title>{CSS}<meta http-equiv='refresh' content='{REFRESH_SEC}'></head><body>
    <div class='wrap'>
        <div class='head'>
            <div><h2>{sec}</h2><div style='font-size:12px;color:var(--muted)'>Stock Rotation & Signals</div></div>
            <div><a href='{BASE_PATH}/' class='btn'>← Back</a> <button class='btn' onclick='toggleTheme()'>◑</button></div>
        </div>
        <div class='card'>
            <div class='table-wrap'>
                <table><thead><tr><th>Stock</th><th>LTP</th><th>VWAP</th><th>Stock%</th><th>Rel%</th><th>Δ5m</th><th>VolΔ5m</th><th>State</th><th>Signal</th></tr></thead><tbody>{body}</tbody></table>
            </div>
        </div>
    </div></body></html>
    """