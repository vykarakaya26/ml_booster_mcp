// Crosshair + tooltip: işaretçiye en yakın X'e yapışır, o tarihteki tüm serileri listeler.
// DOM'a yalnızca textContent ile yazılır (veri asla HTML olarak yorumlanmaz).
(function () {
  function fmt(v) {
    if (v === null || v === undefined) return "—";
    var a = Math.abs(v);
    var digits = a >= 100 ? 1 : a >= 1 ? 2 : 4;
    return v.toLocaleString("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  document.querySelectorAll("figure[data-chart]").forEach(function (fig) {
    var data;
    try { data = JSON.parse(fig.getAttribute("data-chart")); } catch (e) { return; }
    var svg = fig.querySelector("svg");
    var tip = fig.querySelector(".tooltip");
    var cross = svg && svg.querySelector(".crosshair");
    if (!svg || !tip || !cross || !data.px.length) return;
    function nearest(px) {
      var best = 0, dist = Infinity;
      for (var i = 0; i < data.px.length; i++) {
        var d = Math.abs(data.px[i] - px);
        if (d < dist) { dist = d; best = i; }
      }
      return best;
    }
    function show(evt) {
      var rect = svg.getBoundingClientRect();
      var px = (evt.clientX - rect.left) / rect.width * data.w;
      var i = nearest(px);
      cross.setAttribute("x1", data.px[i]); cross.setAttribute("x2", data.px[i]);
      cross.setAttribute("visibility", "visible");
      tip.replaceChildren();
      var head = document.createElement("div"); head.className = "tt-date"; head.textContent = data.x[i].slice(0, 7);
      tip.appendChild(head);
      data.series.forEach(function (s) {
        var row = document.createElement("div"); row.className = "tt-row";
        if (data.series.length > 1) { var sw = document.createElement("span"); sw.className = "swatch s" + s.slot; row.appendChild(sw); }
        var val = document.createElement("strong"); val.textContent = fmt(s.v[i]); row.appendChild(val);
        var lab = document.createElement("span"); lab.textContent = s.label; row.appendChild(lab);
        tip.appendChild(row);
      });
      tip.hidden = false;
      var x = data.px[i] / data.w * rect.width;
      var left = x + 12 + tip.offsetWidth > rect.width ? x - tip.offsetWidth - 12 : x + 12;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = "8px";
    }
    function hide() { tip.hidden = true; cross.setAttribute("visibility", "hidden"); }
    svg.addEventListener("pointermove", show);
    svg.addEventListener("pointerleave", hide);
  });
})();
