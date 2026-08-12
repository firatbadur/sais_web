/**
 * mimic3d/ui/palette.js — sol panel sembol kutuphanesi (2B editorun palet
 * deneyimiyle ayni: kategori baslikari, arama, tikla-ekle, surukle-birak).
 *
 * Ikonlar `symbolThumbURL` ile GERCEK 3B render'dan uretilir (SVG degil) ->
 * palette gordugun sey sahnede goreceginle aynidir. Uretim bir kez yapilir ve
 * sessionStorage'da onbelleklenir.
 *
 * Surukleme MIME tipi `text/mimic3d-sym` — 2B'nin `text/mimic-sym`'inden
 * BILINCLI olarak farkli: iki editor arasi surukleme yarim calismak yerine
 * temiz sekilde basarisiz olsun.
 */

export const DRAG_MIME = "text/mimic3d-sym";

export function makePalette(el, searchEl, opts) {
    const options = opts || {};
    const symbols = options.symbols || [];
    const thumb = options.thumb || (() => "");
    let query = "";

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    }

    function matches(it) {
        if (!query) return true;
        const q = query.toLocaleLowerCase("tr");
        return (it.name || "").toLocaleLowerCase("tr").indexOf(q) >= 0
            || (it.key || "").toLowerCase().indexOf(q) >= 0;
    }

    function render() {
        if (!el) return;
        let html = "";
        let shown = 0;
        symbols.forEach((grp) => {
            const items = grp.items.filter(matches);
            if (!items.length) return;
            html += '<div class="sp-group">' + esc(grp.label) + "</div>";
            html += '<div class="sp-grid">';
            items.forEach((it) => {
                shown += 1;
                html += '<div class="sp-item" draggable="true" data-key="' + esc(it.key) + '"'
                    + ' title="' + esc(it.name) + " · " + esc(it.key) + '">'
                    + '<div class="sp-thumb" data-thumb="' + esc(it.key) + '"></div>'
                    + '<span>' + esc(it.name) + "</span></div>";
            });
            html += "</div>";
        });
        if (!shown) {
            html = '<div class="ol-empty">Sonuç yok. Aramayı temizleyin.</div>';
        }
        el.innerHTML = html;
        // Ikonlari tembel doldur (ilk render'i bloklamasin).
        requestAnimationFrame(() => {
            el.querySelectorAll("[data-thumb]").forEach((d) => {
                const url = thumb(d.dataset.thumb);
                if (url) d.style.backgroundImage = "url(" + url + ")";
            });
        });
    }

    if (el) {
        el.addEventListener("click", (e) => {
            const item = e.target.closest(".sp-item");
            if (item && options.onAdd) options.onAdd(item.dataset.key, null);
        });
        el.addEventListener("dragstart", (e) => {
            const item = e.target.closest(".sp-item");
            if (!item) return;
            e.dataTransfer.setData(DRAG_MIME, item.dataset.key);
            e.dataTransfer.effectAllowed = "copy";
        });
    }
    if (searchEl) {
        searchEl.addEventListener("input", () => { query = searchEl.value || ""; render(); });
    }

    /** Tuval uzerine birakma: zemin isini ile konum bulunur. */
    function bindCanvasDrop(canvas, onDrop) {
        if (!canvas) return;
        canvas.addEventListener("dragover", (e) => {
            if (Array.prototype.indexOf.call(e.dataTransfer.types, DRAG_MIME) < 0) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = "copy";
        });
        canvas.addEventListener("drop", (e) => {
            const key = e.dataTransfer.getData(DRAG_MIME);
            if (!key) return;
            e.preventDefault();
            onDrop(key, e.clientX, e.clientY);
        });
    }

    render();
    return { render: render, bindCanvasDrop: bindCanvasDrop };
}
