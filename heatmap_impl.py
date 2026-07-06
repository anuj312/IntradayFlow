# heatmap_impl.py
#
# Treemap Heatmap builder for TurboTrades
# - Returns Plotly Figure (no Dash app code)
# - No top title text
# - No explicit "MARKET" root node (sectors are top-level: parent="")
# - Removes the grey "root strip" by setting root_color transparent
# - Sector-big / stock-small font is handled by assets/heatmap_fonts.js (JS)

import os
from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go


# =============================================================================
# CONFIG
# =============================================================================
HEATMAP_TOP_N_PER_SECTOR = int(os.getenv("HEATMAP_TOP_N_PER_SECTOR", "18"))
HEATMAP_ADD_OTHERS = os.getenv("HEATMAP_ADD_OTHERS", "1").strip().lower() not in ("0", "false", "no")
HEATMAP_PACKING = os.getenv("HEATMAP_PACKING", "squarify").strip()
HEATMAP_SECTOR_POWER = float(os.getenv("HEATMAP_SECTOR_POWER", "2.5"))

# Stock sizing metric inside sector:
#   - "abs_pct": big movers up/down
#   - "pos_pct": only gainers big
#   - "turnover": turnover proxy
HEATMAP_STOCK_SIZE_METRIC = os.getenv("HEATMAP_STOCK_SIZE_METRIC", "abs_pct").strip()  # abs_pct | pos_pct | turnover
HEATMAP_MAX_STOCK_LABEL_CHARS = int(os.getenv("HEATMAP_MAX_STOCK_LABEL_CHARS", "9"))


# =============================================================================
# HELPERS
# =============================================================================
def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=8, b=8),
        title=None,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        annotations=[
            dict(
                text=msg,
                showarrow=False,
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                font=dict(size=16),
            )
        ],
    )
    return fig


def _unicode_bold_char(c: str) -> str:
    o = ord(c)
    if 65 <= o <= 90:   # A-Z
        return chr(0x1D400 + (o - 65))
    if 97 <= o <= 122:  # a-z
        return chr(0x1D41A + (o - 97))
    if 48 <= o <= 57:   # 0-9
        return chr(0x1D7CE + (o - 48))
    return c


def unicode_bold(s: str) -> str:
    # Plotly treemap tiles don't reliably render <b>...</b>, so use Unicode bold.
    return "".join(_unicode_bold_char(c) for c in str(s))


def heatmap_short_symbol(sym: str, max_len: int = HEATMAP_MAX_STOCK_LABEL_CHARS) -> str:
    sym = str(sym)
    if sym == "OTHERS":
        return sym
    return sym if len(sym) <= max_len else sym[: max_len - 1] + "…"


def _topn_plus_others_heatmap(sdf: pd.DataFrame, n: int, add_others: bool, size_col: str) -> pd.DataFrame:
    """
    sdf must already be sorted.
    Adds an OTHERS leaf:
      - pct color = weighted avg pct by size metric
      - dirr = weighted avg dirr
      - turnover = sum(turnover)
    """
    if n <= 0 or len(sdf) <= n:
        return sdf

    top = sdf.iloc[:n].copy()
    rest = sdf.iloc[n:].copy()

    if add_others and not rest.empty:
        wsum = float(rest[size_col].sum())
        if wsum <= 1e-9:
            w = (rest["abs_pct"] + 0.01).astype(float)
            wsum = float(w.sum())
        else:
            w = rest[size_col].astype(float)

        others_pct = float((rest["pct"].astype(float) * w).sum() / (wsum + 1e-9))
        others_dirr = float((rest["dirr"].astype(float) * w).sum() / (wsum + 1e-9))
        others_turn = float(rest["turnover"].sum())

        top = pd.concat(
            [
                top,
                pd.DataFrame([{
                    "sector_key": str(rest.iloc[0]["sector_key"]),
                    "sector_label": str(rest.iloc[0]["sector_label"]),
                    "symbol": "OTHERS",
                    "pct": others_pct,
                    "dirr": others_dirr,
                    "turnover": others_turn,
                    "abs_pct": float(rest["abs_pct"].sum()),
                    "pos_pct": float(rest["pos_pct"].sum()),
                }])
            ],
            ignore_index=True,
        )

    return top


# =============================================================================
# MAIN FIGURE BUILDER
# =============================================================================
def build_market_heatmap_figure(rows: List[Dict[str, Any]]) -> go.Figure:
    if not rows:
        return _empty_fig("Heatmap warming up…")

    df = pd.DataFrame(rows)

    # If df is shape (1,0) due to rows like [{}], df.empty becomes True
    if df.empty:
        return _empty_fig("Heatmap: no usable data yet")

    required = {"sector_key", "sector_label", "symbol", "pct", "dirr"}
    if not required.issubset(set(df.columns)):
        missing = sorted(required - set(df.columns))
        return _empty_fig(f"Heatmap: missing columns {missing}")

    # Normalize turnover
    if "turnover" not in df.columns:
        if "value" in df.columns:
            df["turnover"] = pd.to_numeric(df["value"], errors="coerce")
        else:
            df["turnover"] = 0.0

    df["pct"] = pd.to_numeric(df["pct"], errors="coerce")
    df["dirr"] = pd.to_numeric(df["dirr"], errors="coerce")
    df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce").fillna(0.0)

    df = df.dropna(subset=["sector_key", "sector_label", "symbol", "pct", "dirr"])
    if df.empty:
        return _empty_fig("Heatmap: waiting for ticks / stats…")

    # Sizing metrics
    df["abs_pct"] = df["pct"].abs()
    df["pos_pct"] = df["pct"].clip(lower=0.0)

    size_col = HEATMAP_STOCK_SIZE_METRIC
    if size_col not in ("abs_pct", "pos_pct", "turnover"):
        size_col = "abs_pct"

    # Sector order by mean momentum (DirR) desc
    sec_mean_dirr = df.groupby("sector_key")["dirr"].mean().to_dict()
    sector_order = sorted(
        df["sector_key"].unique().tolist(),
        key=lambda s: float(sec_mean_dirr.get(s, 0.0)),
        reverse=True,
    )
    if not sector_order:
        return _empty_fig("Heatmap: no sectors to display")

    nsec = len(sector_order)

    # Sector sizes: rank^POWER
    sector_weight: Dict[str, float] = {}
    for i, sec in enumerate(sector_order):
        rank_val = float(max(1, nsec - i))
        sector_weight[sec] = rank_val ** float(HEATMAP_SECTOR_POWER)

    # Color range symmetric around 0
    mx = float(max(0.5, df["pct"].abs().max()))

    # Build WITHOUT explicit root node => no "MARKET" tile
    labels: List[str] = []
    texts: List[str] = []
    ids: List[str] = []
    parents: List[str] = []
    values: List[float] = []
    colors: List[float] = []
    customdata: List[List[float]] = []  # [turnover, dirr, pct]

    for sec in sector_order:
        sdf = df[df["sector_key"] == sec].copy()
        if sdf.empty:
            continue

        # Stock order inside sector by %Change desc (change to "dirr" if you prefer)
        sdf.sort_values("pct", ascending=False, inplace=True)

        sdf = _topn_plus_others_heatmap(
            sdf,
            n=int(HEATMAP_TOP_N_PER_SECTOR),
            add_others=bool(HEATMAP_ADD_OTHERS),
            size_col=size_col,
        )

        sec_label = str(sdf.iloc[0]["sector_label"])
        sec_id = f"sec:{sec}"  # IMPORTANT: JS uses this to enlarge sector labels
        w_sec = float(sector_weight.get(sec, 1.0))

        # Sector node (top-level)
        labels.append(sec_label)
        texts.append(unicode_bold(sec_label))
        ids.append(sec_id)
        parents.append("")  # top-level
        values.append(w_sec)
        colors.append(float(sdf["pct"].mean()))
        customdata.append([
            float(sdf["turnover"].sum()),
            float(sdf["dirr"].mean() if len(sdf) else 0.0),
            float(sdf["pct"].mean() if len(sdf) else 0.0),
        ])

        # Leaf sizing inside sector
        w = sdf[size_col].astype(float)
        if float(w.sum()) <= 1e-9:
            w = (sdf["abs_pct"].astype(float) + 0.01)
        wsum = float(w.sum())

        for (_, r), wi in zip(sdf.iterrows(), w.tolist()):
            sym = str(r["symbol"])
            leaf_area = (float(wi) / (wsum + 1e-9)) * w_sec

            labels.append(sym)
            texts.append(heatmap_short_symbol(sym))
            ids.append(f"{sec}:{sym}")
            parents.append(sec_id)
            values.append(float(leaf_area))
            colors.append(float(r["pct"]))
            customdata.append([float(r["turnover"]), float(r["dirr"]), float(r["pct"])])

    if not labels:
        return _empty_fig("Heatmap: no tiles to render")

    fig = go.Figure(
        go.Treemap(
            labels=labels,
            text=texts,
            texttemplate="%{text}",
            textinfo="text",
            textfont=dict(size=10),  # stocks baseline small; sectors enlarged via JS
            ids=ids,
            parents=parents,
            values=values,
            customdata=customdata,
            marker=dict(
                colors=colors,
                colorscale=[
                    [0.0, "#8b1e2d"],  # red
                    [0.5, "#2b2b2b"],  # neutral
                    [1.0, "#1f9d55"],  # green
                ],
                cmin=-mx,
                cmax=mx,
                cmid=0.0,
                showscale=False,
                line=dict(width=1.2, color="rgba(255,255,255,0.22)"),
            ),
            root_color="rgba(0,0,0,0)",  # <-- removes the grey root strip at top
            branchvalues="total",
            sort=False,
            tiling=dict(packing=HEATMAP_PACKING, pad=2),
            hovertemplate=(
                "<b>%{label}</b>"
                "<br>%Chg: %{color:.2f}%"
                "<br>Turnover: %{customdata[0]:,.0f}"
                "<br>DirR: %{customdata[1]:.2f}"
                "<extra></extra>"
            ),
            pathbar=dict(visible=False),
        )
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=8, b=8),
        uniformtext_minsize=10,
        uniformtext_mode="hide",
        title=None,
    )
    return fig