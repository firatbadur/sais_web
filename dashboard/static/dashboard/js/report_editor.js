/* Rapor Stüdyosu — blok tabanlı şablon editörü.
 *
 * Durum: `state` (şablon meta + blocks dizisi). Sol panel blok listesi
 * (SortableJS ile sürükle-sırala), sağ panel seçili bloğun ayar formu,
 * orta panel canlı önizleme (api_report_preview → iframe.srcdoc).
 * Kaydet: api_report_tpl_save (mimic_screen_save sözleşmesi).
 */
(function () {
    "use strict";

    const CFG = window.RPT_CFG;
    const T = CFG.i18n;

    /* ------------------------------------------------------------------ */
    /* Durum                                                                */
    /* ------------------------------------------------------------------ */

    const state = CFG.initial || {
        id: null,
        name: "",
        description: "",
        page_size: "A4",
        orientation: "portrait",
        header_text: "",
        footer_text: "",
        show_logo: true,
        blocks: [],
    };
    let selectedId = null;      // seçili blok id'si
    let dirty = false;
    let blockSeq = Date.now();  // yeni blok id üretimi

    // İstasyon başına parametre cache'i: {stationId: [{id, text, unit, group}]}
    const paramCache = {};

    const BLOCK_ICONS = {
        heading: "ki-text", text: "ki-notepad", kpi_cards: "ki-element-11",
        chart: "ki-chart-line", table: "ki-row-horizontal",
        spacer: "ki-arrow-up-down", page_break: "ki-scissor",
    };
    const BUCKETS = [
        ["raw", T.raw], ["15min", T.b15], ["hourly", T.hourly], ["daily", T.daily],
    ];
    const AGG_COLS = [
        ["avg", T.colAvg], ["min", T.colMin], ["max", T.colMax],
        ["count", T.colCount], ["bad_count", T.colBad],
    ];

    function newBlockId() { return "b" + (++blockSeq).toString(36); }

    function defaultBlock(type) {
        const base = { id: newBlockId(), type: type };
        if (type === "heading") return { ...base, text: "", level: 2 };
        if (type === "text") return { ...base, text: "" };
        if (type === "kpi_cards") return { ...base, cards: [] };
        if (type === "chart") return {
            ...base, title: "", chart_type: "line",
            binding: { station_id: null, parameter_ids: [], bucket: "hourly",
                       window: { mode: "relative", key: "last_24h" }, columns: ["avg"] },
        };
        if (type === "table") return {
            ...base, title: "",
            binding: { station_id: null, parameter_ids: [], bucket: "hourly",
                       window: { mode: "relative", key: "last_24h" },
                       columns: ["avg", "min", "max"], max_rows: 500 },
        };
        return base; // spacer / page_break
    }

    function markDirty() {
        dirty = true;
        document.getElementById("save-state").textContent = "● " + T.unsaved;
        schedulePreview();
    }

    /* ------------------------------------------------------------------ */
    /* Meta form ↔ state                                                    */
    /* ------------------------------------------------------------------ */

    const META_FIELDS = [
        ["rpt-name", "name"], ["rpt-desc", "description"],
        ["rpt-size", "page_size"], ["rpt-orient", "orientation"],
        ["rpt-header", "header_text"], ["rpt-footer", "footer_text"],
    ];

    function metaToForm() {
        META_FIELDS.forEach(([elId, key]) => {
            document.getElementById(elId).value = state[key] || (elId === "rpt-size" ? "A4" : elId === "rpt-orient" ? "portrait" : "");
        });
        document.getElementById("rpt-logo").checked = state.show_logo !== false;
    }

    function bindMetaForm() {
        META_FIELDS.forEach(([elId, key]) => {
            document.getElementById(elId).addEventListener("input", (e) => {
                state[key] = e.target.value;
                markDirty();
            });
        });
        document.getElementById("rpt-logo").addEventListener("change", (e) => {
            state.show_logo = e.target.checked;
            markDirty();
        });
    }

    /* ------------------------------------------------------------------ */
    /* Blok listesi (sol panel)                                             */
    /* ------------------------------------------------------------------ */

    function blockSummary(b) {
        if (b.type === "heading" || b.type === "text") {
            return (b.text || "").slice(0, 48) || "—";
        }
        if (b.type === "kpi_cards") return (b.cards || []).length + " kart";
        if (b.type === "chart" || b.type === "table") {
            const st = CFG.stations.find(s => s.id === (b.binding || {}).station_id);
            const pc = ((b.binding || {}).parameter_ids || []).length;
            return (b.title ? b.title + " · " : "") + (st ? st.name + " · " : "") + pc + " parametre";
        }
        return "";
    }

    function renderBlockList() {
        const wrap = document.getElementById("block-list");
        wrap.innerHTML = "";
        if (!state.blocks.length) {
            wrap.innerHTML = '<div class="text-muted text-center py-6 fs-8">—</div>';
            return;
        }
        state.blocks.forEach((b) => {
            const div = document.createElement("div");
            div.className = "blk-item" + (b.id === selectedId ? " active" : "");
            div.dataset.blockId = b.id;
            div.innerHTML =
                '<span class="drag-handle"><i class="ki-duotone ki-burger-menu fs-3"><span class="path1"></span><span class="path2"></span><span class="path3"></span><span class="path4"></span></i></span>' +
                '<i class="ki-duotone ' + (BLOCK_ICONS[b.type] || "ki-abstract") + ' fs-3 text-primary"><span class="path1"></span><span class="path2"></span><span class="path3"></span><span class="path4"></span><span class="path5"></span></i>' +
                '<span class="blk-label"><span class="t">' + (T[b.type] || b.type) + '</span>' +
                '<span class="s">' + escapeHtml(blockSummary(b)) + "</span></span>";
            div.addEventListener("click", () => selectBlock(b.id));
            wrap.appendChild(div);
        });
    }

    function escapeHtml(s) {
        return String(s || "").replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    function initSortable() {
        new Sortable(document.getElementById("block-list"), {
            handle: ".drag-handle",
            animation: 150,
            ghostClass: "sortable-ghost",
            onEnd: function (evt) {
                if (evt.oldIndex === evt.newIndex) return;
                const moved = state.blocks.splice(evt.oldIndex, 1)[0];
                state.blocks.splice(evt.newIndex, 0, moved);
                markDirty();
            },
        });
    }

    /* ------------------------------------------------------------------ */
    /* Parametre listesi (AJAX + cache)                                     */
    /* ------------------------------------------------------------------ */

    function fetchParams(stationId) {
        if (!stationId) return Promise.resolve([]);
        if (paramCache[stationId]) return Promise.resolve(paramCache[stationId]);
        return fetch(CFG.urls.stationParams + "?station=" + stationId)
            .then(r => r.json())
            .then(d => {
                const flat = [];
                (d.results || []).forEach(group => {
                    (group.children || []).forEach(ch => {
                        flat.push({ id: ch.id, text: ch.text, unit: ch.unit || "", group: group.text });
                    });
                });
                paramCache[stationId] = flat;
                return flat;
            })
            .catch(() => []);
    }

    /* ------------------------------------------------------------------ */
    /* Ayar paneli (sağ)                                                    */
    /* ------------------------------------------------------------------ */

    // Panel ilk render'da temizlendiği için boş-durum metnini baştan sakla.
    const EMPTY_TEXT = (document.getElementById("settings-empty") || {}).textContent || "";

    function selectBlock(id) {
        selectedId = id;
        renderBlockList();
        renderSettings();
    }

    function currentBlock() {
        return state.blocks.find(b => b.id === selectedId) || null;
    }

    function el(tag, attrs, html) {
        const e = document.createElement(tag);
        Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
        if (html !== undefined) e.innerHTML = html;
        return e;
    }

    function fieldWrap(labelText, inputEl) {
        const d = el("div", { class: "mb-4" });
        d.appendChild(el("label", { class: "form-label" }, labelText));
        d.appendChild(inputEl);
        return d;
    }

    function renderSettings() {
        const panel = document.getElementById("settings-panel");
        const delBtn = document.getElementById("btn-del-block");
        panel.innerHTML = "";
        const b = currentBlock();
        if (!b) {
            panel.innerHTML = '<div class="text-muted text-center py-8">' +
                escapeHtml(EMPTY_TEXT) + "</div>";
            delBtn.classList.add("d-none");
            return;
        }
        delBtn.classList.remove("d-none");

        const head = el("div", { class: "fw-bold fs-6 mb-4" },
            '<i class="ki-duotone ' + (BLOCK_ICONS[b.type] || "") + ' fs-3 text-primary me-2"><span class="path1"></span><span class="path2"></span></i>' +
            (T[b.type] || b.type));
        panel.appendChild(head);

        if (b.type === "heading") {
            const inp = el("input", { class: "form-control", type: "text", value: b.text || "" });
            inp.addEventListener("input", () => { b.text = inp.value; markDirty(); renderBlockList(); });
            panel.appendChild(fieldWrap(T.headingText, inp));

            const lvl = el("select", { class: "form-select" });
            [1, 2, 3].forEach(l => lvl.appendChild(el("option", { value: l }, "H" + l)));
            lvl.value = b.level || 2;
            lvl.addEventListener("change", () => { b.level = parseInt(lvl.value, 10); markDirty(); });
            panel.appendChild(fieldWrap(T.level, lvl));
        }
        else if (b.type === "text") {
            const ta = el("textarea", { class: "form-control", rows: 6 });
            ta.value = b.text || "";
            ta.addEventListener("input", () => { b.text = ta.value; markDirty(); renderBlockList(); });
            panel.appendChild(fieldWrap(T.textContent, ta));
        }
        else if (b.type === "kpi_cards") {
            renderKpiSettings(panel, b);
        }
        else if (b.type === "chart" || b.type === "table") {
            renderBindingSettings(panel, b);
        }
        else {
            panel.appendChild(el("div", { class: "text-muted fs-8" }, T.noBlockNote));
        }
    }

    /* --- Ortak: pencere seçici --- */
    function windowSelector(container, obj, onChange) {
        const sel = el("select", { class: "form-select form-select-sm" });
        CFG.windows.forEach(w => sel.appendChild(el("option", { value: w.key }, escapeHtml(w.label))));
        sel.appendChild(el("option", { value: "__fixed__" }, T.fixedDates));

        const win = obj.window || { mode: "relative", key: "last_24h" };
        sel.value = win.mode === "fixed" ? "__fixed__" : (win.key || "last_24h");
        container.appendChild(fieldWrap(T.window, sel));

        const fixedWrap = el("div", { class: "row g-2 mb-4", style: win.mode === "fixed" ? "" : "display:none" });
        const startInp = el("input", { class: "form-control form-control-sm", type: "datetime-local", value: win.start || "" });
        const endInp = el("input", { class: "form-control form-control-sm", type: "datetime-local", value: win.end || "" });
        const c1 = el("div", { class: "col-6" }); c1.appendChild(startInp);
        const c2 = el("div", { class: "col-6" }); c2.appendChild(endInp);
        fixedWrap.appendChild(c1); fixedWrap.appendChild(c2);
        container.appendChild(fixedWrap);

        function apply() {
            if (sel.value === "__fixed__") {
                fixedWrap.style.display = "";
                obj.window = { mode: "fixed", start: startInp.value, end: endInp.value };
            } else {
                fixedWrap.style.display = "none";
                obj.window = { mode: "relative", key: sel.value };
            }
            onChange();
        }
        sel.addEventListener("change", apply);
        startInp.addEventListener("change", apply);
        endInp.addEventListener("change", apply);
    }

    /* --- chart/table binding formu --- */
    function renderBindingSettings(panel, b) {
        const binding = b.binding = b.binding || {};

        // Blok başlığı
        const titleInp = el("input", { class: "form-control form-control-sm", type: "text", value: b.title || "" });
        titleInp.addEventListener("input", () => { b.title = titleInp.value; markDirty(); renderBlockList(); });
        panel.appendChild(fieldWrap(T.titleLbl, titleInp));

        // Grafik tipi
        if (b.type === "chart") {
            const ct = el("select", { class: "form-select form-select-sm" });
            ct.appendChild(el("option", { value: "line" }, T.line));
            ct.appendChild(el("option", { value: "bar" }, T.bar));
            ct.value = b.chart_type || "line";
            ct.addEventListener("change", () => { b.chart_type = ct.value; markDirty(); });
            panel.appendChild(fieldWrap(T.chartType, ct));
        }

        // İstasyon
        const stSel = el("select", { class: "form-select form-select-sm" });
        stSel.appendChild(el("option", { value: "" }, T.chooseStation));
        CFG.stations.forEach(s => stSel.appendChild(el("option", { value: s.id }, escapeHtml(s.name))));
        stSel.value = binding.station_id || "";
        panel.appendChild(fieldWrap(T.station, stSel));

        // Parametreler (çoklu)
        const pSel = el("select", { class: "form-select form-select-sm", multiple: "multiple", size: 7 });
        panel.appendChild(fieldWrap(T.parameters, pSel));

        function loadParams(keepSelection) {
            pSel.innerHTML = "";
            const sid = parseInt(stSel.value, 10);
            if (!sid) return;
            fetchParams(sid).then(items => {
                if (!items.length) {
                    pSel.appendChild(el("option", { disabled: "disabled" }, T.noParams));
                    return;
                }
                const groups = {};
                items.forEach(p => {
                    if (!groups[p.group]) {
                        groups[p.group] = el("optgroup", { label: p.group });
                        pSel.appendChild(groups[p.group]);
                    }
                    const o = el("option", { value: p.id },
                        escapeHtml(p.text) + (p.unit ? " (" + escapeHtml(p.unit) + ")" : ""));
                    if (keepSelection && (binding.parameter_ids || []).includes(p.id)) o.selected = true;
                    groups[p.group].appendChild(o);
                });
            });
        }
        loadParams(true);

        stSel.addEventListener("change", () => {
            binding.station_id = parseInt(stSel.value, 10) || null;
            binding.parameter_ids = [];
            loadParams(false);
            markDirty(); renderBlockList();
        });
        pSel.addEventListener("change", () => {
            binding.parameter_ids = Array.from(pSel.selectedOptions).map(o => parseInt(o.value, 10));
            markDirty(); renderBlockList();
        });

        // Bucket
        const bSel = el("select", { class: "form-select form-select-sm" });
        BUCKETS.forEach(([v, l]) => bSel.appendChild(el("option", { value: v }, l)));
        bSel.value = binding.bucket || "hourly";
        bSel.addEventListener("change", () => { binding.bucket = bSel.value; markDirty(); });
        panel.appendChild(fieldWrap(T.bucket, bSel));

        // Pencere
        windowSelector(panel, binding, markDirty);

        // Kolonlar (yalnız tablo + aggregate anlamlı; raw'da otomatik)
        if (b.type === "table") {
            const colWrap = el("div", { class: "mb-4" });
            colWrap.appendChild(el("label", { class: "form-label" }, T.columns));
            AGG_COLS.forEach(([v, l]) => {
                const lab = el("label", { class: "form-check form-check-custom form-check-sm mb-1" });
                const cb = el("input", { class: "form-check-input", type: "checkbox", value: v });
                cb.checked = (binding.columns || []).includes(v);
                cb.addEventListener("change", () => {
                    const cols = new Set(binding.columns || []);
                    cb.checked ? cols.add(v) : cols.delete(v);
                    binding.columns = AGG_COLS.map(([cv]) => cv).filter(cv => cols.has(cv));
                    markDirty();
                });
                lab.appendChild(cb);
                lab.appendChild(el("span", { class: "form-check-label fs-8" }, l));
                colWrap.appendChild(lab);
            });
            panel.appendChild(colWrap);

            const mrInp = el("input", { class: "form-control form-control-sm", type: "number",
                                        min: 10, max: 5000, value: binding.max_rows || 500 });
            mrInp.addEventListener("change", () => {
                binding.max_rows = parseInt(mrInp.value, 10) || 500;
                markDirty();
            });
            panel.appendChild(fieldWrap(T.maxRows, mrInp));
        }
    }

    /* --- KPI kartları formu --- */
    function renderKpiSettings(panel, b) {
        b.cards = b.cards || [];
        const list = el("div", {});
        panel.appendChild(el("div", { class: "fw-semibold fs-7 mb-2" }, T.cards));
        panel.appendChild(list);

        const addBtn = el("button", { class: "btn btn-sm btn-light-primary w-100", type: "button" },
            '<i class="ki-duotone ki-plus fs-4"></i>' + T.addCard);
        addBtn.addEventListener("click", () => {
            b.cards.push({ station_id: null, parameter_id: null, agg: "avg",
                           window: { mode: "relative", key: "last_24h" }, label: "" });
            markDirty(); renderBlockList();
            renderCards();
        });
        panel.appendChild(addBtn);

        function renderCards() {
            list.innerHTML = "";
            b.cards.forEach((card, idx) => {
                const row = el("div", { class: "kpi-card-row" });

                // üst satır: sıra + kaldır
                const top = el("div", { class: "d-flex justify-content-between align-items-center mb-2" });
                top.appendChild(el("span", { class: "fw-semibold fs-8" }, "#" + (idx + 1)));
                const rm = el("button", { class: "btn btn-sm btn-icon btn-light-danger", type: "button", title: T.removeCard },
                    '<i class="ki-duotone ki-cross fs-4"><span class="path1"></span><span class="path2"></span></i>');
                rm.addEventListener("click", () => {
                    b.cards.splice(idx, 1);
                    markDirty(); renderBlockList(); renderCards();
                });
                top.appendChild(rm);
                row.appendChild(top);

                // istasyon
                const stSel = el("select", { class: "form-select form-select-sm mb-2" });
                stSel.appendChild(el("option", { value: "" }, T.chooseStation));
                CFG.stations.forEach(s => stSel.appendChild(el("option", { value: s.id }, escapeHtml(s.name))));
                stSel.value = card.station_id || "";
                row.appendChild(stSel);

                // parametre (tekli)
                const pSel = el("select", { class: "form-select form-select-sm mb-2" });
                row.appendChild(pSel);

                function loadCardParams(keep) {
                    pSel.innerHTML = "";
                    pSel.appendChild(el("option", { value: "" }, T.parameter + "..."));
                    const sid = parseInt(stSel.value, 10);
                    if (!sid) return;
                    fetchParams(sid).then(items => {
                        items.forEach(p => {
                            const o = el("option", { value: p.id },
                                escapeHtml(p.text) + (p.unit ? " (" + escapeHtml(p.unit) + ")" : ""));
                            if (keep && card.parameter_id === p.id) o.selected = true;
                            pSel.appendChild(o);
                        });
                    });
                }
                loadCardParams(true);

                stSel.addEventListener("change", () => {
                    card.station_id = parseInt(stSel.value, 10) || null;
                    card.parameter_id = null;
                    loadCardParams(false);
                    markDirty();
                });
                pSel.addEventListener("change", () => {
                    card.parameter_id = parseInt(pSel.value, 10) || null;
                    markDirty(); renderBlockList();
                });

                // agg + pencere
                const aggSel = el("select", { class: "form-select form-select-sm mb-2" });
                [["last", T.aggLast], ["avg", T.aggAvg], ["min", T.aggMin], ["max", T.aggMax]]
                    .forEach(([v, l]) => aggSel.appendChild(el("option", { value: v }, l)));
                aggSel.value = card.agg || "avg";
                aggSel.addEventListener("change", () => { card.agg = aggSel.value; markDirty(); });
                row.appendChild(fieldWrap(T.aggFn, aggSel));

                windowSelector(row, card, markDirty);

                // etiket
                const lblInp = el("input", { class: "form-control form-control-sm", type: "text",
                                             value: card.label || "", placeholder: T.cardLabel });
                lblInp.addEventListener("input", () => { card.label = lblInp.value; markDirty(); });
                row.appendChild(lblInp);

                list.appendChild(row);
            });
        }
        renderCards();
    }

    /* ------------------------------------------------------------------ */
    /* Standalone chrome: sağ panel sekmeleri + Ekle menüsü + tema          */
    /* ------------------------------------------------------------------ */

    function switchTab(name) {
        document.querySelectorAll(".rp-tab").forEach(t =>
            t.classList.toggle("active", t.dataset.tab === name));
        document.querySelectorAll(".rp-pane").forEach(p =>
            p.classList.toggle("active", p.dataset.pane === name));
        if (name === "sched") renderSchedPane();
    }
    document.querySelectorAll(".rp-tab").forEach(t =>
        t.addEventListener("click", () => switchTab(t.dataset.tab)));

    // Blok Ekle açılır menüsü (bootstrap JS yok — custom toggle)
    const addMenu = document.getElementById("add-menu");
    const addBtn = document.getElementById("btn-add-block");
    if (addMenu && addBtn) {
        addBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            addMenu.classList.toggle("open");
        });
        document.addEventListener("click", () => addMenu.classList.remove("open"));
    }

    // Tema değiştirici (dashboard ile paylaşılan localStorage anahtarı)
    const themeBtn = document.getElementById("btn-theme");
    if (themeBtn) {
        themeBtn.addEventListener("click", () => {
            const cur = document.documentElement.getAttribute("data-bs-theme") || "light";
            const next = cur === "dark" ? "light" : "dark";
            document.documentElement.setAttribute("data-bs-theme", next);
            localStorage.setItem("data-bs-theme", next);
        });
    }

    /* ------------------------------------------------------------------ */
    /* Blok ekle / sil                                                      */
    /* ------------------------------------------------------------------ */

    document.querySelectorAll("#add-block-menu [data-add]").forEach((a) => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            if (addMenu) addMenu.classList.remove("open");
            const b = defaultBlock(a.dataset.add);
            state.blocks.push(b);
            markDirty();
            switchTab("block");
            selectBlock(b.id);
        });
    });

    document.getElementById("btn-del-block").addEventListener("click", () => {
        const b = currentBlock();
        if (!b) return;
        if (!confirm(T.confirmDelBlock)) return;
        state.blocks = state.blocks.filter(x => x.id !== b.id);
        selectedId = null;
        markDirty();
        renderBlockList();
        renderSettings();
    });

    /* ------------------------------------------------------------------ */
    /* Önizleme (debounce'lu)                                               */
    /* ------------------------------------------------------------------ */

    let previewTimer = null;
    let previewBusy = false;

    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(runPreview, 900);
    }

    function payload() {
        return {
            id: state.id,
            name: state.name || "Adsız Rapor",
            description: state.description || "",
            page_size: state.page_size,
            orientation: state.orientation,
            header_text: state.header_text || "",
            footer_text: state.footer_text || "",
            show_logo: state.show_logo !== false,
            blocks: state.blocks,
        };
    }

    function runPreview() {
        if (previewBusy) { schedulePreview(); return; }
        previewBusy = true;
        document.getElementById("preview-loading").style.display = "flex";
        fetch(CFG.urls.preview, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": CFG.csrf },
            body: JSON.stringify(payload()),
        })
            .then(r => {
                const ct = r.headers.get("Content-Type") || "";
                if (ct.includes("application/json")) {
                    return r.json().then(d => { throw new Error(d.error || "preview error"); });
                }
                return r.text();
            })
            .then(html => {
                document.getElementById("preview-frame").srcdoc = html;
            })
            .catch(err => {
                document.getElementById("preview-frame").srcdoc =
                    '<div style="font-family:sans-serif;color:#f1416c;padding:24px">' +
                    escapeHtml(err.message) + "</div>";
            })
            .finally(() => {
                previewBusy = false;
                document.getElementById("preview-loading").style.display = "none";
            });
    }

    document.getElementById("btn-preview").addEventListener("click", runPreview);

    /* ------------------------------------------------------------------ */
    /* Kaydet                                                               */
    /* ------------------------------------------------------------------ */

    document.getElementById("btn-save").addEventListener("click", () => {
        if (!(state.name || "").trim()) {
            alert(T.nameRequired);
            document.getElementById("rpt-name").focus();
            return;
        }
        fetch(CFG.urls.save, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": CFG.csrf },
            body: JSON.stringify(payload()),
        })
            .then(r => r.json())
            .then(d => {
                const stateEl = document.getElementById("save-state");
                if (!d.ok) {
                    stateEl.textContent = "✗ " + (d.error || T.saveError);
                    stateEl.classList.add("err");
                    return;
                }
                dirty = false;
                stateEl.classList.remove("err");
                stateEl.textContent = "✓ " + T.saved;
                if (!state.id) {
                    state.id = d.id;
                    // Yeni kayıt — URL'i düzenleme moduna çevir (reload'suz)
                    const editUrl = CFG.urls.editBase + d.id + "/";
                    window.history.replaceState({}, "", editUrl);
                    // Zamanlama sekmesi artık kullanılabilir — açıksa tazele
                    const schedTab = document.querySelector('.rp-tab[data-tab="sched"].active');
                    if (schedTab) renderSchedPane();
                }
            })
            .catch(() => {
                document.getElementById("save-state").textContent = "✗ " + T.saveError;
            });
    });

    window.addEventListener("beforeunload", (e) => {
        if (dirty) { e.preventDefault(); e.returnValue = ""; }
    });

    /* ------------------------------------------------------------------ */
    /* Zamanlama sekmesi — şablonun zamanlamaları + SCADA kullanıcı listesi */
    /* ------------------------------------------------------------------ */

    let usersCache = null;   // [{id, name, email}]
    let schedListCache = null;

    function fetchUsers() {
        if (usersCache) return Promise.resolve(usersCache);
        return fetch(CFG.urls.recipients)
            .then(r => r.json())
            .then(d => {
                usersCache = (d.results || []).map(u => ({
                    id: u.id,
                    name: (u.text || "").split(" — ")[0],
                    email: u.email || "",
                }));
                return usersCache;
            })
            .catch(() => []);
    }

    function fetchSchedules() {
        return fetch(CFG.urls.schedList + "?template_id=" + state.id)
            .then(r => r.json())
            .then(d => { schedListCache = d.results || []; return schedListCache; })
            .catch(() => []);
    }

    function renderSchedPane() {
        const pane = document.getElementById("sched-pane");
        if (!pane) return;
        if (!state.id) {
            pane.innerHTML = '<div class="empty-note">' + escapeHtml(T.schedSaveFirst) + "</div>";
            return;
        }
        pane.innerHTML = '<div class="empty-note"><span class="spinner-border spinner-border-sm"></span></div>';
        fetchSchedules().then(renderSchedList);
    }

    function renderSchedList(items) {
        const pane = document.getElementById("sched-pane");
        pane.innerHTML = "";

        const newBtn = el("button", { class: "mini-btn", type: "button", style: "margin-bottom:10px" },
            '<i class="ki-duotone ki-plus" style="font-size:14px"></i>' + T.schedNew);
        newBtn.addEventListener("click", () => renderSchedForm(null));
        pane.appendChild(newBtn);

        if (!items.length) {
            pane.appendChild(el("div", { class: "empty-note" }, escapeHtml(T.schedNone)));
            return;
        }

        items.forEach(s => {
            const row = el("div", { class: "kpi-card-row" });
            const head = el("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:6px" });
            head.appendChild(el("span", { style: "font-weight:600;color:var(--mx-head);font-size:12.5px" },
                escapeHtml(s.period_summary)));
            const badges = el("span", { style: "display:flex;gap:4px" });
            badges.appendChild(el("span", {
                class: "badge " + (s.enabled ? "badge-light-success" : "badge-light"),
            }, s.enabled ? T.schedEnabled : T.schedOff));
            head.appendChild(badges);
            row.appendChild(head);

            const info = el("div", { style: "font-size:11px;color:var(--mx-muted);margin:4px 0 8px" },
                (s.output_pdf ? "PDF " : "") + (s.output_excel ? "Excel " : "") +
                (s.email_enabled ? "· ✉ " : "") +
                (s.next_run_at ? "· " + T.schedNext + ": " + s.next_run_at : ""));
            row.appendChild(info);

            const btns = el("div", { style: "display:flex;gap:6px" });
            const eBtn = el("button", { class: "mini-btn", type: "button" }, T.schedEdit);
            eBtn.addEventListener("click", () => renderSchedForm(s));
            const dBtn = el("button", { class: "mini-btn danger", type: "button" }, T.schedDelete);
            dBtn.addEventListener("click", () => {
                if (!confirm(T.schedConfirmDel)) return;
                fetch(CFG.urls.schedDelete, {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-CSRFToken": CFG.csrf },
                    body: JSON.stringify({ id: s.id }),
                }).then(r => r.json()).then(d => { if (d.ok) renderSchedPane(); });
            });
            btns.appendChild(eBtn);
            btns.appendChild(dBtn);
            row.appendChild(btns);
            pane.appendChild(row);
        });
    }

    function renderSchedForm(sched) {
        const pane = document.getElementById("sched-pane");
        pane.innerHTML = "";
        const s = sched || {
            id: null, enabled: true, period: "daily", time_of_day: "07:00",
            weekday: 0, day_of_month: 1, output_pdf: true, output_excel: false,
            email_enabled: false, recipients: "",
            email_subject: "{report_name} - {date}", email_body: "",
        };

        // Periyot
        const perSel = el("select", { class: "form-select" });
        [["daily", T.schedDaily], ["weekly", T.schedWeekly], ["monthly", T.schedMonthly]]
            .forEach(([v, l]) => perSel.appendChild(el("option", { value: v }, l)));
        perSel.value = s.period;
        pane.appendChild(fieldWrap(T.schedPeriod, perSel));

        // Haftanın günü / ayın günü (koşullu)
        const wdWrap = el("div", { class: "mb-4", style: s.period === "weekly" ? "" : "display:none" });
        const wdSel = el("select", { class: "form-select" });
        T.weekdays.forEach((l, i) => wdSel.appendChild(el("option", { value: i }, escapeHtml(l))));
        wdSel.value = s.weekday === null || s.weekday === undefined ? 0 : s.weekday;
        wdWrap.appendChild(el("label", { class: "form-label" }, T.schedWeekday));
        wdWrap.appendChild(wdSel);
        pane.appendChild(wdWrap);

        const domWrap = el("div", { class: "mb-4", style: s.period === "monthly" ? "" : "display:none" });
        const domInp = el("input", { class: "form-control", type: "number", min: 1, max: 28,
                                     value: s.day_of_month || 1 });
        domWrap.appendChild(el("label", { class: "form-label" }, T.schedDom));
        domWrap.appendChild(domInp);
        pane.appendChild(domWrap);

        perSel.addEventListener("change", () => {
            wdWrap.style.display = perSel.value === "weekly" ? "" : "none";
            domWrap.style.display = perSel.value === "monthly" ? "" : "none";
        });

        // Saat + etkin
        const timeInp = el("input", { class: "form-control", type: "time", value: s.time_of_day });
        pane.appendChild(fieldWrap(T.schedTime, timeInp));

        const enLab = el("label", { class: "form-check form-switch form-check-custom mb-4", style: "gap:8px" });
        const enCb = el("input", { class: "form-check-input", type: "checkbox" });
        enCb.checked = s.enabled !== false;
        enLab.appendChild(enCb);
        enLab.appendChild(el("span", { class: "form-check-label" }, T.schedEnabled));
        pane.appendChild(enLab);

        // Formatlar
        const fmtWrap = el("div", { class: "mb-4" });
        fmtWrap.appendChild(el("label", { class: "form-label" }, T.schedFormats));
        const pdfCb = el("input", { class: "form-check-input", type: "checkbox" });
        pdfCb.checked = s.output_pdf !== false;
        const xlsCb = el("input", { class: "form-check-input", type: "checkbox" });
        xlsCb.checked = !!s.output_excel;
        [["PDF", pdfCb], ["Excel", xlsCb]].forEach(([lbl, cb]) => {
            const lab = el("label", { class: "form-check form-check-custom form-check-sm mb-1", style: "gap:8px" });
            lab.appendChild(cb);
            lab.appendChild(el("span", { class: "form-check-label" }, lbl));
            fmtWrap.appendChild(lab);
        });
        pane.appendChild(fmtWrap);

        // E-posta
        const emLab = el("label", { class: "form-check form-switch form-check-custom mb-4", style: "gap:8px" });
        const emCb = el("input", { class: "form-check-input", type: "checkbox" });
        emCb.checked = !!s.email_enabled;
        emLab.appendChild(emCb);
        emLab.appendChild(el("span", { class: "form-check-label" }, T.schedEmail));
        pane.appendChild(emLab);

        const emailWrap = el("div", { style: emCb.checked ? "" : "display:none" });
        pane.appendChild(emailWrap);
        emCb.addEventListener("change", () => {
            emailWrap.style.display = emCb.checked ? "" : "none";
            if (emCb.checked) loadUserList();
        });

        // SCADA kullanıcı listesi (checkbox) + ek alıcılar
        const userBox = el("div", { class: "mb-4" });
        userBox.appendChild(el("label", { class: "form-label" }, T.schedUsers));
        const userList = el("div", {
            style: "max-height:180px;overflow-y:auto;border:1px solid var(--mx-border);" +
                   "border-radius:7px;padding:8px;background:var(--mx-panel-2)",
        });
        userBox.appendChild(userList);
        emailWrap.appendChild(userBox);

        const extraTa = el("textarea", { class: "form-control", rows: 2,
                                         placeholder: "ornek@firma.com, ..." });
        emailWrap.appendChild(fieldWrap(T.schedExtra, extraTa));

        const subjInp = el("input", { class: "form-control", type: "text",
                                      value: s.email_subject || "{report_name} - {date}" });
        emailWrap.appendChild(fieldWrap(T.schedSubject, subjInp));

        const bodyTa = el("textarea", { class: "form-control", rows: 3 });
        bodyTa.value = s.email_body || "";
        emailWrap.appendChild(fieldWrap(T.schedBody, bodyTa));

        const selectedEmails = new Set();
        const existing = (s.recipients || "").split(/[,;\n]+/).map(x => x.trim()).filter(Boolean);

        function loadUserList() {
            userList.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
            fetchUsers().then(users => {
                userList.innerHTML = "";
                const known = new Set();
                users.forEach(u => {
                    const lab = el("label", {
                        class: "form-check form-check-custom form-check-sm",
                        style: "gap:8px;margin-bottom:5px;opacity:" + (u.email ? "1" : ".5"),
                    });
                    const cb = el("input", { class: "form-check-input", type: "checkbox" });
                    if (!u.email) cb.disabled = true;
                    else {
                        known.add(u.email.toLowerCase());
                        if (existing.some(e => e.toLowerCase() === u.email.toLowerCase())) {
                            cb.checked = true;
                            selectedEmails.add(u.email);
                        }
                        cb.addEventListener("change", () => {
                            cb.checked ? selectedEmails.add(u.email) : selectedEmails.delete(u.email);
                        });
                    }
                    lab.appendChild(cb);
                    lab.appendChild(el("span", { class: "form-check-label", style: "font-size:12px" },
                        escapeHtml(u.name) + " — " +
                        (u.email ? escapeHtml(u.email) : "<i>" + T.schedNoEmail + "</i>")));
                    userList.appendChild(lab);
                });
                // Kullanıcı listesinde olmayan mevcut alıcılar → ek alıcılar alanına
                const extras = existing.filter(e => !known.has(e.toLowerCase()));
                extraTa.value = extras.join(", ");
            });
        }
        if (emCb.checked) loadUserList();

        // Hata + kaydet/vazgeç
        const errBox = el("div", { class: "empty-note", style: "display:none;color:#e4544c;padding:8px" });
        pane.appendChild(errBox);

        const btnRow = el("div", { style: "display:flex;gap:8px;margin-top:8px" });
        const saveBtn = el("button", { class: "mini-btn", type: "button", style: "flex:1;justify-content:center;padding:9px" }, T.schedSave);
        const cancelBtn = el("button", { class: "mini-btn", type: "button",
            style: "flex:1;justify-content:center;padding:9px;background:var(--mx-panel-2);color:var(--mx-text);border:1px solid var(--mx-border)" }, T.schedCancel);
        cancelBtn.addEventListener("click", renderSchedPane);
        saveBtn.addEventListener("click", () => {
            const recipients = Array.from(selectedEmails)
                .concat(extraTa.value.split(/[,;\n]+/).map(x => x.trim()).filter(Boolean))
                .join(", ");
            const payload = {
                id: s.id,
                template_id: state.id,
                enabled: enCb.checked,
                period: perSel.value,
                time_of_day: timeInp.value || "07:00",
                weekday: parseInt(wdSel.value, 10),
                day_of_month: parseInt(domInp.value, 10),
                output_pdf: pdfCb.checked,
                output_excel: xlsCb.checked,
                email_enabled: emCb.checked,
                recipients: recipients,
                email_subject: subjInp.value,
                email_body: bodyTa.value,
            };
            fetch(CFG.urls.schedSave, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": CFG.csrf },
                body: JSON.stringify(payload),
            }).then(r => r.json()).then(d => {
                if (!d.ok) {
                    errBox.textContent = d.error || T.saveError;
                    errBox.style.display = "";
                    return;
                }
                renderSchedPane();
            });
        });
        btnRow.appendChild(saveBtn);
        btnRow.appendChild(cancelBtn);
        pane.appendChild(btnRow);
    }

    /* ------------------------------------------------------------------ */
    /* Başlat                                                               */
    /* ------------------------------------------------------------------ */

    metaToForm();
    bindMetaForm();
    renderBlockList();
    renderSettings();
    initSortable();
    if (state.blocks.length) {
        selectBlock(state.blocks[0].id);
    }
    runPreview();
})();
