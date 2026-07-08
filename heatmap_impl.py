# heatmap_impl.py
#
# Plotly Treemap Heatmap builder
# - Tight packing (no weird empty remainder space)
# - Leaf colors: deep red/green like your screenshot (custom mapping)
# - Sector headers are drawn in JS (assets/heatmap_fonts.js)

import os
from typing import Any, Dict, List, Tuple

import pandas as pd
import plotly.graph_objects as go


HEATMAP_TOP_N_PER_SECTOR = int(os.getenv("HEATMAP_TOP_N_PER_SECTOR", "18"))
HEATMAP_ADD_OTHERS = os.getenv("HEATMAP_ADD_OTHERS", "0").strip().lower() not in ("0", "false", "no")
HEATMAP_PACKING = os.getenv("HEATMAP_PACKING", "squarify").strip()
HEATMAP_SECTOR_POWER = float(os.getenv("HEATMAP_SECTOR_POWER", "2.5"))

# abs_pct | pos_pct | turnover
HEATMAP_STOCK_SIZE_METRIC = os.getenv("HEATMAP_STOCK_SIZE_METRIC", "abs_pct").strip()
HEATMAP_MAX_STOCK_LABEL_CHARS = int(os.getenv("HEATMAP_MAX_STOCK_LABEL_CHARS", "9"))

_BG = "#000000"
_LINE = "#000000"


def _empty_fig(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        margin=dict(l=6, r=6, t=6, b=6),
        title=None,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        annotations=[dict(text=msg, showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")],
    )
    return fig


def _unicode_bold_char(c: str) -> str:
    o = ord(c)
    if 65 <= o <= 90:
        return chr(0x1D400 + (o - 65))
    if 97 <= o <= 122:
        return chr(0x1D41A + (o - 97))
    if 48 <= o <= 57:
        return chr(0x1D7CE + (o - 48))
    return c


def unicode_bold(s: str) -> str:
    return "".join(_unicode_bold_char(c) for c in str(s))


def heatmap_short_symbol(sym: str, max_len: int = HEATMAP_MAX_STOCK_LABEL_CHARS) -> str:
    sym = str(sym)
    if sym == "OTHERS":
        return sym
    return sym if len(sym) <= max_len else sym[: max_len - 1] + "…"


def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _lerp_color(c0: str, c1: str, t: float) -> str:
    t = max(0.0, min(1.0, float(t)))
    r0, g0, b0 = _hex_to_rgb(c0)
    r1, g1, b1 = _hex_to_rgb(c1)
    return _rgb_to_hex(_lerp(r0, r1, t), _lerp(g0, g1, t), _lerp(b0, b1, t))


def pct_to_color(pct: float, mx: float) -> str:
    """
    Deep red for negatives, bright green for positives, near-black around 0.
    Matches the "mostly maroon" look with a few green pops.
    """
    if mx <= 1e-9:
        return "#151515"

    v = float(pct)
    t = min(1.0, abs(v) / mx)

    # make small moves darker; big moves pop more
    t = t ** 0.65

    if v > 0:
        return _lerp_color("#0b2416", "#1fa83f", t)
    if v < 0:
        return _lerp_color("#2a1417", "#9b2f3a", t)

    return "#111111"


def _topn_plus_others_heatmap(sdf: pd.DataFrame, n: int, add_others: bool, size_col: str) -> pd.DataFrame:
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
                }]),
            ],
            ignore_index=True,
        )

    return top


def build_market_heatmap_figure(rows: List[Dict[str, Any]]) -> go.Figure:
    if not rows:
        return _empty_fig("Heatmap warming up…")

    df = pd.DataFrame(rows)
    if df.empty:
        return _empty_fig("Heatmap: no data yet")

    required = {"sector_key", "sector_label", "symbol", "pct", "dirr"}
    if not required.issubset(set(df.columns)):
        return _empty_fig(f"Heatmap: missing {sorted(required - set(df.columns))}")

    if "turnover" not in df.columns:
        df["turnover"] = pd.to_numeric(df.get("value"), errors="coerce").fillna(0.0)

    df["pct"] = pd.to_numeric(df["pct"], errors="coerce")
    df["dirr"] = pd.to_numeric(df["dirr"], errors="coerce")
    df["turnover"] = pd.to_numeric(df["turnover"], errors="coerce").fillna(0.0)

    df = df.dropna(subset=["sector_key", "sector_label", "symbol", "pct", "dirr"])
    if df.empty:
        return _empty_fig("Heatmap: waiting…")

    df["abs_pct"] = df["pct"].abs()
    df["pos_pct"] = df["pct"].clip(lower=0.0)

    size_col = HEATMAP_STOCK_SIZE_METRIC
    if size_col not in ("abs_pct", "pos_pct", "turnover"):
        size_col = "abs_pct"

    # Sector order: ABS(mean DirR) desc, tie: + before -
    sec_mean_dirr = df.groupby("sector_key")["dirr"].mean().to_dict()

    def _v(sec: str) -> float:
        try:
            return float(sec_mean_dirr.get(sec, 0.0) or 0.0)
        except Exception:
            return 0.0

    sector_order = sorted(sec_mean_dirr.keys(), key=lambda s: (abs(_v(s)), _v(s)), reverse=True)
    if not sector_order:
        return _empty_fig("Heatmap: no sectors")

    # Sector area by rank^power
    nsec = len(sector_order)
    sector_weight: Dict[str, float] = {}
    for i, sec in enumerate(sector_order):
        rank_val = float(max(1, nsec - i))
        sector_weight[sec] = rank_val ** float(HEATMAP_SECTOR_POWER)

    mx = float(max(0.5, df["pct"].abs().max()))

    labels: List[str] = []
    texts: List[str] = []
    ids: List[str] = []
    parents: List[str] = []
    values: List[float] = []
    colors: List[str] = []
    customdata: List[List[float]] = []  # [turnover, dirr, pct]

    for sec in sector_order:
        sdf = df[df["sector_key"] == sec].copy()
        if sdf.empty:
            continue

        # STOCK sort: %Change DESC (as you want)
        sdf.sort_values("pct", ascending=False, inplace=True)

        sdf = _topn_plus_others_heatmap(
            sdf,
            n=int(HEATMAP_TOP_N_PER_SECTOR),
            add_others=bool(HEATMAP_ADD_OTHERS),
            size_col=size_col,
        )

        sec_label = str(sdf.iloc[0]["sector_label"])
        sec_id = f"sec:{sec}"
        w_sec = float(sector_weight.get(sec, 1.0))

        # sector node (container)
        labels.append(sec_label)
        texts.append(unicode_bold(sec_label))     # JS will hide this and draw header bar
        ids.append(sec_id)
        parents.append("")
        values.append(w_sec)
        colors.append("#000000")
        customdata.append([float(sdf["turnover"].sum()), float(sdf["dirr"].mean()), float(sdf["pct"].mean())])

        # children sum must equal sector value for branchvalues="total"
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
            colors.append(pct_to_color(float(r["pct"]), mx))
            customdata.append([float(r["turnover"]), float(r["dirr"]), float(r["pct"])])

    if not labels:
        return _empty_fig("Heatmap: nothing to render")

    fig = go.Figure(
        go.Treemap(
            labels=labels,
            text=texts,
            texttemplate="%{text}",
            textinfo="text",
            textfont=dict(size=10, color="#ffffff"),
            ids=ids,
            parents=parents,
            values=values,
            customdata=customdata,
            marker=dict(colors=colors, line=dict(width=2.0, color=_LINE)),
            branchvalues="total",
            sort=False,
            tiling=dict(packing=HEATMAP_PACKING, pad=1),
            hovertemplate=(
                "<b>%{label}</b>"
                "<br>% chg: %{customdata[2]:.2f}%"
                "<br>Turnover: %{customdata[0]:,.0f}"
                "<br>DirR: %{customdata[1]:.2f}"
                "<extra></extra>"
            ),
            pathbar=dict(visible=False),
            root_color=_BG,
        )
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        margin=dict(l=6, r=6, t=6, b=6),
        uniformtext_minsize=9,
        uniformtext_mode="hide",
        title=None,
        hoverlabel=dict(
            bgcolor="#ffffff",
            bordercolor="#e6e6e6",
            font=dict(color="#111111", size=16),
        ),
    )
    return fig