// assets/heatmap_fonts.js
// - Root strip stays black
// - Sector header bar + sector name
// - Stock names centered, visible, not bold, fit/ellipsis

(function () {
  const HEADER_H_PX = 24;
  const HEADER_PAD_X = 10;
  const HEADER_FONT = 12;

  const STOCK_FONT_MAX = 11;
  const STOCK_FONT_MIN = 7;
  const STOCK_MIN_W = 22;
  const STOCK_MIN_H = 14;

  function clearOld(layer) {
    const old = layer.querySelector("#tt-overlays");
    if (old) old.remove();
  }

  function safeDecode(x) {
    try { return decodeURIComponent(String(x || "")); }
    catch { return String(x || ""); }
  }

  function ellipsize(s, n) {
    s = String(s || "");
    if (!s) return "";
    if (s.length <= n) return s;
    return s.slice(0, Math.max(1, n - 1)) + "…";
  }

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function apply(container) {
    const treemapLayer =
      container.querySelector(".main-svg .treemaplayer") ||
      container.querySelector(".treemaplayer");

    if (!treemapLayer) return;

    const traceGroup = treemapLayer.querySelector(".trace") || treemapLayer;
    clearOld(traceGroup);

    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "g");
    overlay.setAttribute("id", "tt-overlays");
    overlay.style.pointerEvents = "none";
    traceGroup.appendChild(overlay);

    const slices = container.querySelectorAll(".treemaplayer .trace .slice");

    slices.forEach((slice) => {
      const d = slice.__data__ || {};
      const data = d.data || d || {};

      const id = safeDecode(data.id ?? d.id ?? "");
      const parent = safeDecode(data.parent ?? d.parent ?? "");
      const depth =
        (data.depth !== undefined) ? Number(data.depth) :
        (d.depth !== undefined) ? Number(d.depth) : NaN;

      const path = slice.querySelector("path, rect");
      const textEl = slice.querySelector("text");

      // Grab Plotly-rendered label BEFORE hiding it (most reliable)
      const plotlyText = (textEl && textEl.textContent) ? textEl.textContent.trim() : "";
      const label = String(data.label ?? d.label ?? plotlyText ?? "");

      // ROOT: force black
      const isRoot =
        depth === 0 ||
        id === "root" ||
        (parent === "" && !id.startsWith("sec:") && !label);

      if (isRoot) {
        slice.style.pointerEvents = "none";
        if (path) {
          path.style.fill = "#000000";
          path.style.stroke = "#000000";
          path.style.pointerEvents = "none";
        }
        if (textEl) textEl.style.display = "none";
        return;
      }

      // SECTOR: by id prefix (fallback: parent=="" & depth==1)
      const isSector = id.startsWith("sec:") || (parent === "" && depth === 1);

      if (isSector) {
        if (textEl) textEl.style.display = "none";
        if (!path) return;

        const bbox = path.getBBox();
        if (!bbox || bbox.width < 70 || bbox.height < 40) return;

        const h = Math.min(HEADER_H_PX, Math.max(18, bbox.height * 0.18));

        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", String(bbox.x));
        rect.setAttribute("y", String(bbox.y));
        rect.setAttribute("width", String(bbox.width));
        rect.setAttribute("height", String(h));
        rect.setAttribute("fill", "#000000");
        overlay.appendChild(rect);

        const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
        t.setAttribute("x", String(bbox.x + HEADER_PAD_X));
        t.setAttribute("y", String(bbox.y + h - 7));
        t.setAttribute("fill", "#ffffff");
        t.setAttribute("font-size", String(HEADER_FONT));
        t.setAttribute("font-weight", "850");
        t.setAttribute("letter-spacing", "0.5px");
        t.textContent = (label || id.replace(/^sec:/, "")).toUpperCase();
        overlay.appendChild(t);
        return;
      }

      // STOCK: center label
      if (!path) return;
      const bbox = path.getBBox();
      if (!bbox) return;

      if (textEl) textEl.style.display = "none";
      if (bbox.width < STOCK_MIN_W || bbox.height < STOCK_MIN_H) return;

      const cx = bbox.x + bbox.width / 2;
      const cy = bbox.y + bbox.height / 2;

      const fontPx = clamp(Math.floor(bbox.height * 0.38), STOCK_FONT_MIN, STOCK_FONT_MAX);
      const pad = 6;
      const availW = Math.max(10, bbox.width - pad * 2);

      const estChars = Math.floor(availW / (fontPx * 0.62));
      const txt = ellipsize(label, Math.max(2, estChars));
      if (!txt) return;

      const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t.setAttribute("x", String(cx));
      t.setAttribute("y", String(cy));
      t.setAttribute("fill", "#ffffff");
      t.setAttribute("font-size", String(fontPx));
      t.setAttribute("font-weight", "200"); // not bold
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("dominant-baseline", "middle");
      t.textContent = txt;
      overlay.appendChild(t);
    });
  }

  function install() {
    const wrap = document.getElementById("market-heatmap");
    if (!wrap) return false;

    const gd = wrap.querySelector(".js-plotly-plot");
    if (!gd || typeof gd.on !== "function") return false;

    if (gd.__ttHeatmapInstalled) return true;
    gd.__ttHeatmapInstalled = true;

    const run = () => apply(wrap);

    gd.on("plotly_afterplot", run);
    gd.on("plotly_redraw", run);
    gd.on("plotly_relayout", run);
    gd.on("plotly_hover", () => setTimeout(run, 0));
    gd.on("plotly_unhover", () => setTimeout(run, 0));

    setTimeout(run, 0);
    return true;
  }

  const t = setInterval(() => {
    if (install()) clearInterval(t);
  }, 300);
})();