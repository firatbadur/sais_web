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
    /* Blok ekle / sil                                                      */
    /* ------------------------------------------------------------------ */

    document.querySelectorAll("#add-block-menu [data-add]").forEach((a) => {
        a.addEventListener("click", (e) => {
            e.preventDefault();
            const b = defaultBlock(a.dataset.add);
            state.blocks.push(b);
            markDirty();
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
                    stateEl.classList.add("text-danger");
                    return;
                }
                dirty = false;
                stateEl.classList.remove("text-danger");
                stateEl.textContent = "✓ " + T.saved;
                if (!state.id) {
                    state.id = d.id;
                    // Yeni kayıt — URL'i düzenleme moduna çevir (reload'suz)
                    const editUrl = CFG.urls.editBase + d.id + "/";
                    window.history.replaceState({}, "", editUrl);
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
