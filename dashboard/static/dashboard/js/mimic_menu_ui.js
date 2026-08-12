/* ===========================================================================
 * mimic_menu_ui.js — Mimik goruntuleyicilerinin PAYLASILAN etkilesim katmani
 * ---------------------------------------------------------------------------
 * 2B (Fabric) ve 3B (Three.js) viewer'lari bu dosyayi paylasir. Iceriginde
 * renderer'a dair HICBIR SEY yoktur; yalniz "bir etiket icin ne gosterilir":
 *
 *   - tiklama aksiyon menusu   (Historik Trend / Veri Raporu / Gunluk Ozet / Kontrol)
 *   - rapor modali             (DataTables + kopyala/CSV/Excel/PDF/yazdir + ApexCharts)
 *   - kontrol diyalogu         (aktif/pasif + deger ata -> api_mimic_control)
 *   - "baska mimik ac"         (modal iframe / yeni sekme / ayni sekme)
 *
 * Iki viewer YALNIZ "tiklanan nesneyi nasil buldugu" ile ayrisir: 2B Fabric
 * event'i, 3B raycaster. Ikisi de sonucta `openMenu(scada, x, y)` cagirir.
 *
 * DOM: `dashboard/mimic/_menu_ui.html` include'u (ayni id'ler).
 * Ceviriler: ayni include'un kurdugu `window.MIMIC_MENU_I18N` (bu JS dosyasinda
 * Django sablon etiketi BULUNAMAZ; sozluk sablonda kurulur).
 *
 * Kullanim:
 *   var menuUI = MimicMenuUI.init({
 *       reportDataUrl, controlUrl, viewerTpl, csrf, canControl,
 *       getMeta: function (tag) { return TAGMETA[tag]; }
 *   });
 *   menuUI.openMenu(scadaObj, clientX, clientY);
 * ======================================================================== */
(function () {
    "use strict";

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
        });
    }

    function init(cfg) {
        var CFG = cfg || {};
        var I18N = window.MIMIC_MENU_I18N || {};
        var getMeta = CFG.getMeta || function () { return {}; };

        // ----------------------------------------------------------------- //
        // Tiklama aksiyon menusu
        // ----------------------------------------------------------------- //
        var menuEl = $("vm-menu");

        function hideMenu() { if (menuEl) menuEl.classList.remove("show"); }

        /**
         * @param {object} sc  objenin `scada` blogu (tag + menu bayraklari)
         * @param {number} x,y ekran koordinati (clientX/clientY)
         * @returns {boolean} menu acildi mi (gosterilecek satir yoksa false)
         */
        function openMenu(sc, x, y) {
            if (!menuEl || !sc) return false;
            var mn = sc.menu || {}, tag = sc.tag;
            if (!tag) return false;
            var meta = getMeta(tag) || {};
            var label = meta.label || tag;
            var rows = [];
            if (mn.historic) rows.push({ ic: "📈", txt: I18N.historic, fn: function () { openDataReport(tag, "hourly", I18N.historic); } });
            if (mn.report)   rows.push({ ic: "📋", txt: I18N.report,   fn: function () { openDataReport(tag, "raw", I18N.report); } });
            if (mn.daily)    rows.push({ ic: "📊", txt: I18N.daily,    fn: function () { openDataReport(tag, "daily", I18N.daily); } });
            // Kontrol yalniz operator/admin + dijital cikis etiketinde; oncesine ayrac.
            if (mn.control && CFG.canControl) {
                rows.push({ ic: "⚙️", txt: I18N.control, sep: rows.length > 0, fn: function () { openControl(tag, meta); } });
            }
            if (!rows.length) return false;   // gosterilecek oge yok

            var html = '<div class="vm-title">' + esc(label)
                + '<small>' + esc(tag) + (meta.station ? " · " + esc(meta.station) : "") + '</small></div>';
            rows.forEach(function (r, i) {
                if (r.sep) html += '<div class="vm-sep"></div>';
                html += '<div class="vm-item" data-i="' + i + '"><span class="ic">' + r.ic + '</span>' + esc(r.txt) + '</div>';
            });
            menuEl.innerHTML = html;
            menuEl.querySelectorAll(".vm-item").forEach(function (el) {
                el.addEventListener("click", function () {
                    var r = rows[parseInt(el.dataset.i, 10)];
                    hideMenu();
                    if (r) r.fn();
                });
            });
            menuEl.classList.add("show");
            var mw = menuEl.offsetWidth || 220, mh = menuEl.offsetHeight || 200;
            menuEl.style.left = Math.min(x, window.innerWidth - mw - 8) + "px";
            menuEl.style.top = Math.min(y, window.innerHeight - mh - 8) + "px";
            return true;
        }

        document.addEventListener("mousedown", function (e) {
            if (menuEl && menuEl.classList.contains("show") && !menuEl.contains(e.target)) hideMenu();
        });

        // ----------------------------------------------------------------- //
        // Rapor modali (tablo + export + grafik) -> api_mimic_report
        // ----------------------------------------------------------------- //
        var dt = null;            // DataTables ornegi
        var rChart = null;        // ApexCharts ornegi
        var lastReport = null;    // son yuklenen rapor verisi (grafik icin)
        var chartOn = false;
        var chartBtnNode = null;  // buton cubugundaki Grafik/Tablo dugmesi

        function openDataReport(tag, kind, title) {
            var meta = getMeta(tag) || {};
            $("vm-rtitle").innerHTML = esc(title) + ' <small>' + esc(meta.label || tag)
                + (meta.station ? " · " + esc(meta.station) : "") + '</small>';
            $("vm-rloading").style.display = "block";
            $("vm-rloading").textContent = I18N.loading;
            showChart(false);
            var tbl = $("vm-rtable");
            tbl.style.display = "none";
            if (dt) { dt.destroy(); tbl.innerHTML = ""; dt = null; }
            $("vm-rovl").classList.add("show");

            var url = CFG.reportDataUrl + "?tag=" + encodeURIComponent(tag)
                + "&kind=" + encodeURIComponent(kind);
            fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (!d || !d.ok) { $("vm-rloading").textContent = (d && d.error) || I18N.error; return; }
                    lastReport = d;
                    buildReportTable(d);
                })
                .catch(function () { $("vm-rloading").textContent = I18N.unreachable; });
        }

        function buildReportTable(d) {
            var unit = d.unit ? " (" + d.unit + ")" : "";
            var head = "<thead><tr>";
            d.columns.forEach(function (c) {
                var lbl = c;
                if (unit && (c === I18N.colValue || c === I18N.colAvg || c === "Min" || c === "Max")) lbl = c + unit;
                head += "<th>" + esc(lbl) + "</th>";
            });
            head += "</tr></thead>";
            // raw:  [display, ts, value, quality, statusText, cls]
            // agg:  [display, ts, avg, min, max, count]
            var raw = d.kind === "raw", body = "<tbody>";
            d.rows.forEach(function (row) {
                body += '<tr><td data-order="' + row[1] + '">' + esc(row[0]) + "</td>";
                if (raw) {
                    var cls = row[5] || "secondary";
                    body += "<td>" + (row[2] == null ? "" : esc(row[2])) + "</td>";
                    body += '<td><span class="badge badge-light-' + cls + '">' + esc(row[3] || "—") + "</span></td>";
                    body += '<td><span class="badge badge-light-' + cls + '">' + esc(row[4] || "—") + "</span></td>";
                } else {
                    for (var i = 2; i < row.length; i++) body += "<td>" + (row[i] == null ? "" : esc(row[i])) + "</td>";
                }
                body += "</tr>";
            });
            body += "</tbody>";
            var tbl = $("vm-rtable");
            tbl.innerHTML = head + body;
            $("vm-rloading").style.display = "none";
            tbl.style.display = "";

            var fname = (d.label || "rapor") + "_" + d.kind;
            var DT_L10N = Object.assign({
                paginate: { first: "«", last: "»", next: "›", previous: "‹" },
            }, I18N.dt || {});
            dt = window.jQuery(tbl).DataTable({
                dom: "<'mb-3'B>t<'row'<'col-sm-5'i><'col-sm-7'p>>",   // arama (f) ve sutunlar (colvis) yok
                order: [[0, "desc"]],
                pageLength: 25,
                lengthChange: false,
                language: DT_L10N,
                buttons: [
                    { extend: "copyHtml5", text: I18N.copy, className: "btn btn-sm btn-light-primary" },
                    { extend: "csvHtml5", text: "CSV", filename: fname, className: "btn btn-sm btn-light-primary" },
                    { extend: "excelHtml5", text: "Excel", filename: fname, className: "btn btn-sm btn-light-success" },
                    { extend: "pdfHtml5", text: "PDF", filename: fname, orientation: "landscape", className: "btn btn-sm btn-light-danger" },
                    { extend: "print", text: I18N.print, className: "btn btn-sm btn-light-info" },
                    // Buton sirasinin en sagi (CSS margin-left:auto) -> Grafik/Tablo gecisi
                    {
                        text: I18N.chart, className: "btn btn-sm btn-light-info dt-chartbtn",
                        action: function () { toggleChart(); },
                    },
                ],
            });
            chartBtnNode = document.querySelector("#vm-rtable_wrapper .dt-chartbtn");
            updateChartBtnLabel();
        }

        // --- Grafik gorunumu (rapor sayfasindaki ApexCharts deseni) ---
        // Tablo + sayfalama gizlenir; buton cubugu (Grafik/Tablo dahil) gorunur kalir.
        function showChart(on) {
            chartOn = on;
            var w = document.getElementById("vm-rtable_wrapper");
            if (w) {
                for (var i = 0; i < w.children.length; i++) {
                    if (i === 0) continue;                 // 0 = buton cubugu -> hep gorunur
                    w.children[i].style.display = on ? "none" : "";
                }
            }
            $("vm-rchartwrap").style.display = on ? "block" : "none";
            updateChartBtnLabel();
        }
        function updateChartBtnLabel() {
            if (chartBtnNode) chartBtnNode.textContent = chartOn ? I18N.table : I18N.chart;
        }
        function toggleChart() {
            if (!lastReport) return;
            if (!chartOn) { buildChartFromReport(lastReport); showChart(true); }
            else { showChart(false); }
        }

        function buildChartFromReport(d) {
            var el = $("vm-rchart-el");
            // raw -> value (index 2); agg -> ortalama (index 2). ts = index 1.
            var pts = [];
            d.rows.forEach(function (row) {
                var v = parseFloat(row[2]);
                if (row[1] && isFinite(v)) pts.push([row[1], v]);
            });
            pts.sort(function (a, b) { return a[0] - b[0]; });
            if (rChart) { rChart.destroy(); rChart = null; }
            el.innerHTML = "";
            if (!pts.length) {
                el.innerHTML = '<div style="text-align:center;color:var(--mx-muted);padding:40px;">'
                    + esc(I18N.noChartData) + '</div>';
                return;
            }
            var isDark = (document.documentElement.getAttribute("data-bs-theme") || "light") === "dark";
            var axisColor = isDark ? "#92929F" : "#5E6278", gridColor = isDark ? "#2B2B40" : "#E4E6EF";
            rChart = new ApexCharts(el, {
                chart: {
                    type: "line", height: 440, fontFamily: "inherit", background: "transparent",
                    toolbar: { show: true, tools: { download: true, zoom: true, zoomin: true, zoomout: true, pan: true, reset: true }, autoSelected: "zoom" },
                    animations: { enabled: true, speed: 500 },
                    zoom: { enabled: true, type: "x", autoScaleYaxis: true },
                },
                theme: { mode: isDark ? "dark" : "light" },
                series: [{ name: (d.label || "") + (d.unit ? " (" + d.unit + ")" : ""), data: pts }],
                stroke: { curve: "smooth", width: 3, lineCap: "round" },
                markers: { size: 0, hover: { size: 6 } },
                dataLabels: { enabled: false },
                grid: { borderColor: gridColor, strokeDashArray: 4, padding: { top: 10, right: 16, left: 12 } },
                xaxis: {
                    type: "datetime", axisBorder: { color: gridColor }, axisTicks: { color: gridColor },
                    labels: {
                        style: { colors: axisColor, fontSize: "12px" }, datetimeUTC: false,
                        datetimeFormatter: { year: "yyyy", month: "MMM 'yy", day: "dd MMM", hour: "HH:mm", minute: "HH:mm:ss" },
                    },
                },
                yaxis: { labels: { formatter: function (v) { return Number(v).toFixed(2); }, style: { colors: axisColor, fontSize: "12px" } } },
                colors: ["#009EF7"],
                tooltip: {
                    theme: isDark ? "dark" : "light", x: { format: "dd.MM.yyyy HH:mm:ss" },
                    y: { formatter: function (v) { return v == null ? "—" : Number(v).toFixed(3); } },
                },
            });
            rChart.render();
        }

        function closeReport() {
            $("vm-rovl").classList.remove("show");
            if (dt) { dt.destroy(); $("vm-rtable").innerHTML = ""; dt = null; }
            if (rChart) { rChart.destroy(); rChart = null; }
            lastReport = null; chartOn = false; chartBtnNode = null;
        }

        // ----------------------------------------------------------------- //
        // Kontrol diyalogu (aktif/pasif + deger ata) -> api_mimic_control
        // ----------------------------------------------------------------- //
        var ctlTag = null, ctlMeta = null;

        function openControl(tag, meta) {
            ctlTag = tag;
            ctlMeta = meta || getMeta(tag) || {};
            $("vm-dtitle").firstChild.textContent = I18N.control + " — " + (ctlMeta.label || tag);
            $("vm-dsub").textContent = tag + (ctlMeta.station ? " · " + ctlMeta.station : "");
            setNote("", "");
            refreshCur();
            var out = !!ctlMeta.is_output;
            $("vm-bon").disabled = $("vm-boff").disabled = $("vm-setbtn").disabled = !out;
            if (!out) setNote(I18N.notctrl, "err");
            $("vm-overlay").classList.add("show");
        }
        function refreshCur() {
            var meta = ctlTag ? (getMeta(ctlTag) || {}) : {};
            var v = meta.value;
            $("vm-dcur").innerHTML = esc(I18N.current) + ": <b>" + (v == null ? "—" : esc(v)) + "</b>";
        }
        function closeControl() { $("vm-overlay").classList.remove("show"); ctlTag = null; }
        function setNote(txt, cls) {
            var n = $("vm-note");
            n.textContent = txt;
            n.className = "vm-note" + (cls ? " " + cls : "");
        }
        function sendControl(action, value) {
            if (!ctlTag) return;
            setNote(I18N.sending, "");
            var body = { tag: ctlTag, action: action };
            if (action === "set") body.value = value;
            fetch(CFG.controlUrl, {
                method: "POST", credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json", "X-CSRFToken": CFG.csrf,
                    "X-Requested-With": "XMLHttpRequest",
                },
                body: JSON.stringify(body),
            }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (res) {
                    if (res.ok && res.d.ok) setNote(res.d.message || I18N.sent, "ok");
                    else setNote((res.d && res.d.error) || I18N.cerror, "err");
                }).catch(function () { setNote(I18N.cerror, "err"); });
        }

        // ----------------------------------------------------------------- //
        // "Baska Mimik Ac" -> modal (iframe) / yeni sekme / ayni sekme
        // 2B<->3B CAPRAZ calisir: tek `mimic_viewer` route'u kind'a gore dogru
        // sablonu secer, burada tur bilgisi gerekmez.
        // ----------------------------------------------------------------- //
        function openMimicLink(sc) {
            var id = sc && sc.linkTarget;
            if (!id) return;
            var url = CFG.viewerTpl.replace(/\/0\/$/, "/" + id + "/");
            var mode = sc.linkMode || "modal";
            if (mode === "newtab") window.open(url, "_blank");
            else if (mode === "same") window.location.href = url;
            else { $("vm-miframe").src = url; $("vm-movl").classList.add("show"); }
        }
        function closeMimicModal() {
            $("vm-movl").classList.remove("show");
            $("vm-miframe").src = "about:blank";
        }

        // ----------------------------------------------------------------- //
        // Olay baglamalari
        // ----------------------------------------------------------------- //
        $("vm-rclose").addEventListener("click", closeReport);
        $("vm-rovl").addEventListener("click", function (e) { if (e.target === $("vm-rovl")) closeReport(); });
        $("vm-bon").addEventListener("click", function () { sendControl("start"); });
        $("vm-boff").addEventListener("click", function () { sendControl("stop"); });
        $("vm-setbtn").addEventListener("click", function () {
            var v = parseFloat($("vm-setval").value);
            if (isNaN(v)) { setNote(I18N.badValue, "err"); return; }
            sendControl("set", v);
        });
        $("vm-dclose").addEventListener("click", closeControl);
        $("vm-overlay").addEventListener("click", function (e) { if (e.target === $("vm-overlay")) closeControl(); });
        $("vm-mclose").addEventListener("click", closeMimicModal);
        $("vm-movl").addEventListener("click", function (e) { if (e.target === $("vm-movl")) closeMimicModal(); });

        return {
            openMenu: openMenu,
            hideMenu: hideMenu,
            openDataReport: openDataReport,
            openControl: openControl,
            openMimicLink: openMimicLink,
            closeAll: function () { hideMenu(); closeReport(); closeControl(); closeMimicModal(); },
            /** Kontrol diyalogu acikken canli degeri tazele (poll sonrasi cagrilir). */
            refreshCurrent: refreshCur,
            esc: esc,
        };
    }

    window.MimicMenuUI = { init: init, esc: esc };
})();
