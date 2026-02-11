// assets/dashAgGridComponentFunctions.js
// Clean + safe global component/formatter registry for Dash AG Grid

(function () {
  const w = window;

  // Ensure registry exists
  const dagcomponentfuncs =
    (w.dashAgGridComponentFunctions = w.dashAgGridComponentFunctions || {});

  // -----------------------------
  // Formatters (also exposed for valueFormatter strings)
  // -----------------------------
  function toNum(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function fmt2(v) {
    const n = toNum(v);
    return n === null ? "" : n.toFixed(2);
  }

  function fmtSigned2(v) {
    const n = toNum(v);
    if (n === null) return "";
    return (n >= 0 ? "+" : "") + n.toFixed(2);
  }

  function fmtPct(v) {
    const n = toNum(v);
    if (n === null) return "";
    return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
  }

  // India compact volume: K / L / Cr
  function fmtVolCompactIN(v) {
    const n = toNum(v);
    if (n === null) return "";
    const a = Math.abs(n);
    if (a >= 1e7) return (n / 1e7).toFixed(2) + "Cr";
    if (a >= 1e5) return (n / 1e5).toFixed(2) + "L";
    if (a >= 1e3) return (n / 1e3).toFixed(2) + "K";
    return String(Math.round(n));
  }

  // Expose for Dash valueFormatter usage (strings)
  w.fmt2 = fmt2;
  w.fmtSigned2 = fmtSigned2;
  w.fmtPct = fmtPct;
  w.fmtVolCompactIN = fmtVolCompactIN;

  // -----------------------------
  // Helpers
  // -----------------------------
  function getReact() {
    // Dash AG Grid uses window.React in most setups
    return w.React;
  }

  function tvUrlFor(sym) {
    return (
      "https://www.tradingview.com/chart/?symbol=" +
      encodeURIComponent("NSE:" + sym) +
      "&interval=5"
    );
  }

  // -----------------------------
  // Cell renderers
  // -----------------------------

  // Simple symbol link
  dagcomponentfuncs.SymbolCell = function (params) {
    const React = getReact();
    if (!React) return params.value ?? "";

    const sym = params.value || "";
    const url = tvUrlFor(sym);

    return React.createElement(
      "a",
      {
        href: url,
        target: "_blank",
        rel: "noopener noreferrer",
        className: "stock-sym",
        onClick: function (e) {
          e.stopPropagation();
        },
      },
      sym
    );
  };

  // Stock cell: symbol + company (2 lines)
  dagcomponentfuncs.StockCell = function (params) {
    const React = getReact();
    if (!React) return params.value ?? "";

    const sym = params.value || "";
    const name = (params.data && params.data.Company) ? params.data.Company : "";
    const url = tvUrlFor(sym);

    return React.createElement("div", { className: "stock-cell" }, [
      React.createElement(
        "a",
        {
          key: "sym",
          href: url,
          target: "_blank",
          rel: "noopener noreferrer",
          className: "stock-sym",
          onClick: function (e) {
            e.stopPropagation();
          },
          title: sym,
        },
        sym
      ),
      React.createElement(
        "div",
        { key: "nm", className: "stock-name", title: name },
        name
      ),
    ]);
  };

  // %Change pill
  dagcomponentfuncs.PctPill = function (params) {
    const React = getReact();
    if (!React) return params.value ?? "";

    const v = toNum(params.value);
    if (v === null) {
      return React.createElement("span", { className: "val-pill neutral" }, "—");
    }

    const cls = v > 0 ? "val-pill up" : (v < 0 ? "val-pill down" : "val-pill neutral");
    const arrow = v > 0 ? "▲ " : (v < 0 ? "▼ " : "• ");
    return React.createElement("span", { className: cls }, arrow + fmtPct(v));
  };

  // RFactor pill
  dagcomponentfuncs.RfactorPill = function (params) {
    const React = getReact();
    if (!React) return params.value ?? "";

    const v = toNum(params.value);
    if (v === null) {
      return React.createElement("span", { className: "val-pill rf neutral" }, "—");
    }
    return React.createElement("span", { className: "val-pill rf" }, fmt2(v) + "×");
  };

  // Volume pill
  dagcomponentfuncs.VolPill = function (params) {
    const React = getReact();
    if (!React) return params.value ?? "";

    const v = toNum(params.value);
    if (v === null) {
      return React.createElement("span", { className: "val-pill vol neutral" }, "—");
    }
    return React.createElement("span", { className: "val-pill vol" }, fmtVolCompactIN(v));
  };
})();