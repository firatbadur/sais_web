/**
 * mimic3d/ui/outliner.js — sahne agaci (2B'deki duz "Katman" listesinin
 * 3B karsiligi).
 *
 * 3B'de nesneler ic ice gruplanabildigi icin duz liste yeterli degil: agac
 * gosterilir, satirlar caret ile katlanir. Her satir: tip ikonu, ad (cift
 * tiklayarak yeniden adlandir), goz (visible), kilit (locked -> ne secilir ne
 * gizmo kabul eder). Secim iki yonlu senkrondur (viewport <-> agac).
 */

const ICONS = {
    group: "ki-folder", primitive: "ki-cube-2", symbol: "ki-cube-3",
    label: "ki-text", button: "ki-toggle-on-circle", sceneBinding: "ki-technology-4",
    import: "ki-file-down",
};

export function makeOutliner(el, opts) {
    const options = opts || {};
    const collapsed = new Set();

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    }

    function rowHtml(o, depth, selectedIds) {
        const id = o.userData.docId;
        const t = o.userData.docType || "primitive";
        const kids = o.children.filter((c) => c.userData && c.userData.docId);
        const isCollapsed = collapsed.has(id);
        const sel = selectedIds.indexOf(id) >= 0;
        let h = '<div class="ol-row' + (sel ? " sel" : "") + '" data-id="' + id
            + '" style="padding-left:' + (6 + depth * 14) + 'px">';
        h += kids.length
            ? '<span class="ol-caret' + (isCollapsed ? " closed" : "") + '" data-act="caret">'
              + (isCollapsed ? "▸" : "▾") + "</span>"
            : '<span class="ol-caret ol-caret-empty"></span>';
        h += '<i class="ki-outline ' + (ICONS[t] || "ki-cube-2") + ' ol-ico"></i>';
        h += '<span class="ol-name" data-act="rename" title="' + esc(o.name) + '">'
            + esc(o.name) + "</span>";
        h += '<span class="ol-btn' + (o.visible === false ? " off" : "")
            + '" data-act="vis" title="Görünürlük">' + (o.visible === false ? "🚫" : "👁") + "</span>";
        h += '<span class="ol-btn' + (o.userData.locked ? " on" : "")
            + '" data-act="lock" title="Kilit">' + (o.userData.locked ? "🔒" : "🔓") + "</span>";
        h += "</div>";
        if (!isCollapsed) {
            // Ters sirada gosterme YOK: 3B'de z-order olmadigi icin agac
            // dogal (ekleme) sirasinda okunur.
            kids.forEach((c) => { h += rowHtml(c, depth + 1, selectedIds); });
        }
        return h;
    }

    function refresh(contentRoot, selection) {
        if (!el) return;
        const selectedIds = selection.list().map((o) => o.userData.docId);
        const roots = contentRoot.children.filter((c) => c.userData && c.userData.docId);
        if (!roots.length) {
            el.innerHTML = '<div class="ol-empty">Sahne boş — soldaki kütüphaneden '
                + 'sembol ekleyin veya şeritten bir şekil oluşturun.</div>';
            return;
        }
        el.innerHTML = roots.map((o) => rowHtml(o, 0, selectedIds)).join("");
    }

    if (el) {
        el.addEventListener("click", (e) => {
            const row = e.target.closest(".ol-row");
            if (!row) return;
            const id = row.dataset.id;
            const act = e.target.dataset ? e.target.dataset.act : null;
            if (act === "caret") {
                if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
                if (options.onRefresh) options.onRefresh();
                return;
            }
            if (act === "vis") { if (options.onToggleVisible) options.onToggleVisible(id); return; }
            if (act === "lock") { if (options.onToggleLock) options.onToggleLock(id); return; }
            if (options.onSelect) options.onSelect(id, e.ctrlKey || e.metaKey || e.shiftKey);
        });
        el.addEventListener("dblclick", (e) => {
            const nameEl = e.target.closest(".ol-name");
            if (!nameEl) return;
            const row = nameEl.closest(".ol-row");
            if (!row || !options.onRename) return;
            const cur = nameEl.textContent;
            const next = window.prompt("Nesne adı:", cur);
            if (next != null && next.trim() && next !== cur) options.onRename(row.dataset.id, next.trim());
        });
    }

    return { refresh: refresh, collapsed: collapsed };
}
