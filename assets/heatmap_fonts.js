// assets/heatmap_fonts.js
// sector big, stock small + force treemap ROOT strip to black

(function () {
  function applyTreemapTweaks(container) {
    try {
      const slices = container.querySelectorAll(".treemaplayer .trace .slice");
      slices.forEach((slice) => {
        const d = slice.__data__ || {};
        const id = (d.data && d.data.id) ? String(d.data.id) : String(d.id || "");
        const depth = (d.data && d.data.depth !== undefined) ? Number(d.data.depth) : Number(d.depth);

        const path = slice.querySelector("path, rect");
        const textEl = slice.querySelector("text");

        // ROOT (depth 0): this is the grey strip you are seeing
        if (depth === 0 || id === "root" || id === "") {
          if (path) {
            path.style.fill = "#000000";
            path.style.stroke = "#000000";
          }
          if (textEl) textEl.style.display = "none";
          return;
        }

        // Sector (your ids are "sec:XXXX")
        const isSector = id.startsWith("sec:");

        if (textEl) {
          textEl.style.fontSize = isSector ? "15px" : "10px";   // sector a bit smaller
          textEl.style.fontWeight = isSector ? "800" : "500";
        }
      });
    } catch (e) {
      // ignore
    }
  }

  function install() {
    const wrap = document.getElementById("market-heatmap");
    if (!wrap) return false;

    const gd = wrap.querySelector(".js-plotly-plot");
    if (!gd || typeof gd.on !== "function") return false;

    if (gd.__ttHeatmapInstalled) return true;
    gd.__ttHeatmapInstalled = true;

    const run = () => applyTreemapTweaks(wrap);

    gd.on("plotly_afterplot", run);
    gd.on("plotly_redraw", run);
    gd.on("plotly_relayout", run);

    setTimeout(run, 0);
    return true;
  }

  const t = setInterval(() => {
    if (install()) clearInterval(t);
  }, 300);
})();