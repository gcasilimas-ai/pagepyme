/* PAGE-PYME · comportamiento del lado del cliente */
(function () {
  "use strict";

  // ---------- navegación móvil y selector de empresa ----------
  document.querySelectorAll("[data-menu]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var lateral = document.getElementById("lateral");
      var abierto = lateral.classList.toggle("abierto");
      boton.setAttribute("aria-expanded", abierto ? "true" : "false");
    });
  });
  document.addEventListener("click", function (e) {
    var lateral = document.getElementById("lateral");
    if (lateral && lateral.classList.contains("abierto") && !lateral.contains(e.target) && !e.target.closest("[data-menu]")) {
      lateral.classList.remove("abierto");
    }
  });
  document.querySelectorAll("[data-autoenviar]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });
  document.querySelectorAll("[data-confirmar]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirmar"))) { e.preventDefault(); }
    });
  });
  var periodo = document.querySelector("[data-periodo]");
  if (periodo) {
    var personalizado = document.querySelector("[data-personalizado]");
    var sincronizar = function () {
      personalizado.hidden = periodo.value !== "personalizado";
      if (periodo.value !== "personalizado") { periodo.form.submit(); }
    };
    personalizado.hidden = periodo.value !== "personalizado";
    periodo.addEventListener("change", sincronizar);
  }

  // ---------- formato ----------
  var miles = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });
  var unDecimal = new Intl.NumberFormat("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  var cop = function (v) { return "$ " + miles.format(v); };
  var copCorto = function (v) {
    var a = Math.abs(v);
    if (a >= 1e6) { return "$ " + unDecimal.format(v / 1e6) + " M"; }
    if (a >= 1e3) { return "$ " + miles.format(v / 1e3) + " mil"; }
    return "$ " + miles.format(v);
  };

  // ---------- líneas de documentos (ventas y cotizaciones) ----------
  var contenedor = document.querySelector("[data-items]");
  if (contenedor) {
    var catalogo = JSON.parse(document.getElementById("catalogo").textContent);
    var total = document.getElementById("total-items");
    var plantilla = document.getElementById("plantilla-item").innerHTML;
    var totalForms = document.getElementById("id_items-TOTAL_FORMS");

    var recalcular = function () {
      var suma = 0;
      contenedor.querySelectorAll("[data-fila]").forEach(function (fila) {
        var borrar = fila.querySelector("input[name$='-DELETE']");
        if (borrar && borrar.checked) { return; }
        var cant = parseFloat(fila.querySelector("input[name$='-cantidad']").value) || 0;
        var precio = parseFloat(fila.querySelector("input[name$='-precio_unitario']").value) || 0;
        var sub = cant * precio;
        fila.querySelector("[data-subtotal]").textContent = sub ? cop(sub) : "—";
        suma += sub;
        var sel = fila.querySelector("select[name$='-producto']");
        var info = catalogo[sel.value];
        var aviso = fila.querySelector("[data-stock]");
        if (info) {
          aviso.textContent = "Disponibles: " + miles.format(info.stock) + " " + info.unidad + (info.stock !== 1 ? "s" : "");
          aviso.classList.toggle("error-campo", contenedor.dataset.venta === "1" && cant > info.stock);
        } else {
          aviso.textContent = "";
        }
      });
      total.textContent = cop(suma);
    };

    var conectar = function (fila) {
      var sel = fila.querySelector("select[name$='-producto']");
      var precio = fila.querySelector("input[name$='-precio_unitario']");
      sel.addEventListener("change", function () {
        var info = catalogo[sel.value];
        if (info) { precio.value = parseFloat(info.precio).toFixed(0); }
        recalcular();
      });
      fila.querySelectorAll("input").forEach(function (i) { i.addEventListener("input", recalcular); i.addEventListener("change", recalcular); });
      var quitar = fila.querySelector("[data-quitar]");
      if (quitar) {
        quitar.addEventListener("click", function () {
          var borrar = fila.querySelector("input[name$='-DELETE']");
          if (borrar) { borrar.checked = true; }
          fila.hidden = true;
          recalcular();
        });
      }
    };

    contenedor.querySelectorAll("[data-fila]").forEach(conectar);
    document.querySelector("[data-agregar]").addEventListener("click", function () {
      var indice = parseInt(totalForms.value, 10);
      var html = plantilla.replace(/__prefix__/g, indice);
      var envoltura = document.createElement("tbody");
      envoltura.innerHTML = html.trim();
      var fila = envoltura.firstElementChild;
      contenedor.appendChild(fila);
      totalForms.value = indice + 1;
      conectar(fila);
      fila.querySelector("select").focus();
    });
    recalcular();
  }

  // ---------- gráficas del tablero ----------
  if (typeof Chart === "undefined") { return; }
  var datos = function (id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  };
  var SERIE = "#1b5faa";
  var TINTA = "#17202b";
  var TENUE = "#667185";
  var REJILLA = "#eceef1";
  Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = TENUE;
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = TINTA;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 6;
  Chart.defaults.plugins.tooltip.displayColors = false;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 400 };

  var ejeValores = function (formato, extra) {
    return Object.assign({
      beginAtZero: true,
      grid: { color: REJILLA, drawTicks: false },
      border: { display: false },
      ticks: { padding: 8, callback: formato, maxTicksLimit: 5 },
    }, extra || {});
  };
  var ejeCategorias = { grid: { display: false }, border: { color: "#dfe3e8" }, ticks: { padding: 6 } };

  var semanal = datos("datos-semanal");
  if (semanal && document.getElementById("g-ventas")) {
    new Chart(document.getElementById("g-ventas"), {
      type: "bar",
      data: {
        labels: semanal.map(function (s) { return s.etiqueta; }),
        datasets: [{ data: semanal.map(function (s) { return s.ingresos; }), backgroundColor: SERIE,
          borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", maxBarThickness: 34 }],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { x: ejeCategorias, y: ejeValores(copCorto) },
        plugins: { tooltip: { callbacks: {
          title: function (it) { var s = semanal[it[0].dataIndex]; return "Semana del " + s.etiqueta + " (" + s.semana + ")"; },
          label: function (it) { return "Ventas: " + cop(it.parsed.y); },
        } } },
      },
    });

    var meta = parseFloat(document.getElementById("g-margen").dataset.meta);
    var valores = semanal.map(function (s) { return s.margen; });
    new Chart(document.getElementById("g-margen"), {
      type: "line",
      data: {
        labels: semanal.map(function (s) { return s.etiqueta; }),
        datasets: [
          { label: "Margen", data: valores, borderColor: SERIE, backgroundColor: SERIE, borderWidth: 2,
            pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: "#fff", pointBorderWidth: 2,
            spanGaps: true, tension: 0 },
          { label: "Meta", data: semanal.map(function () { return meta; }), borderColor: TINTA, borderWidth: 1.5,
            borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 0 },
        ],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: {
          x: ejeCategorias,
          y: ejeValores(function (v) { return v + " %"; }, { beginAtZero: false, suggestedMin: Math.max(0, meta - 15), suggestedMax: meta + 15 }),
        },
        plugins: { tooltip: { callbacks: {
          title: function (it) { var s = semanal[it[0].dataIndex]; return "Semana del " + s.etiqueta + " (" + s.semana + ")"; },
          label: function (it) {
            if (it.parsed.y === null) { return it.dataset.label + ": sin ventas"; }
            return it.dataset.label + ": " + unDecimal.format(it.parsed.y) + " %";
          },
        } } },
      },
    });
  }

  var barrasHorizontales = function (lienzo, etiquetas, valores, titulo) {
    return new Chart(lienzo, {
      type: "bar",
      data: { labels: etiquetas, datasets: [{ data: valores, backgroundColor: SERIE,
        borderRadius: { topRight: 4, bottomRight: 4 }, borderSkipped: "left", maxBarThickness: 22 }] },
      options: {
        indexAxis: "y",
        scales: {
          x: ejeValores(copCorto),
          y: { grid: { display: false }, border: { color: "#dfe3e8" }, ticks: { color: TINTA, autoSkip: false,
            callback: function (v) { var t = this.getLabelForValue(v); return t.length > 34 ? t.slice(0, 33) + "…" : t; } } },
        },
        plugins: { tooltip: { callbacks: {
          title: function (it) { return etiquetas[it[0].dataIndex]; },
          label: function (it) { return titulo + ": " + cop(it.parsed.x); },
        } } },
      },
    });
  };

  var cartera = datos("datos-cartera");
  if (cartera && document.getElementById("g-cartera")) {
    barrasHorizontales(document.getElementById("g-cartera"),
      cartera.map(function (c) { return c.tramo; }), cartera.map(function (c) { return c.saldo; }), "Saldo");
  }
  var top = datos("datos-top");
  if (top && document.getElementById("g-top")) {
    barrasHorizontales(document.getElementById("g-top"),
      top.map(function (t) { return t.producto; }), top.map(function (t) { return t.contribucion; }), "Contribución");
  }
})();
