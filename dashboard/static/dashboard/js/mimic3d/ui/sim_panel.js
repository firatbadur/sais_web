/**
 * mimic3d/ui/sim_panel.js — simulasyon paneli (2B editordeki #sim-panel dengi).
 *
 * Uc surucu, ayni 2B davranisi:
 *   - elle: her bagli etiket icin bir kaydirici
 *   - otomatik: 1500 ms'de bir rastgele 0-100
 *   - canli: `api_mimic_tags` 4000 ms'de bir (gercek sensor degerleri)
 *
 * Simulasyon acikken nesneler secilemez/tasinamaz (gizmo gizlenir, editor
 * "izleme" moduna gecer); butonlar TIKLANABILIR kalir.
 */

export function makeSimPanel(els, opts) {
    const options = opts || {};
    const runtime = options.runtime;
    const onStart = options.onStart || function () {};
    const onStop = options.onStop || function () {};
    const tagsUrl = options.tagsUrl;
    const toast = options.toast || function () {};

    let autoTimer = null;
    let liveTimer = null;

    function buildRows() {
        const host = els.tags;
        if (!host) return;
        const list = runtime.tagList();
        if (!list.length) {
            host.innerHTML = '<div class="sim-empty">Bağlı etiket yok. Animasyon sekmesinden '
                + 'bir nesneye etiket atayın.</div>';
            return;
        }
        host.innerHTML = list.map((t) => (
            '<div class="sim-row"><span class="sim-tag" title="' + t.replace(/"/g, "&quot;") + '">'
            + t.replace(/</g, "&lt;") + '</span>'
            + '<input type="range" min="0" max="100" step="1" value="0" data-tag="'
            + t.replace(/"/g, "&quot;") + '">'
            + '<b class="sim-val">0</b></div>'
        )).join("");
        host.querySelectorAll("input[type=range]").forEach((r) => {
            r.addEventListener("input", () => {
                runtime.setTag(r.dataset.tag, parseFloat(r.value));
                const b = r.parentElement.querySelector(".sim-val");
                if (b) b.textContent = r.value;
            });
        });
    }

    function syncRows(values) {
        const host = els.tags;
        if (!host) return;
        host.querySelectorAll("input[type=range]").forEach((r) => {
            const v = values[r.dataset.tag];
            if (v == null) return;
            r.value = v;
            const b = r.parentElement.querySelector(".sim-val");
            if (b) b.textContent = (Math.round(Number(v) * 100) / 100);
        });
    }

    function stopAuto() { if (autoTimer) { clearInterval(autoTimer); autoTimer = null; } }
    function stopLive() { if (liveTimer) { clearInterval(liveTimer); liveTimer = null; } }

    function startAuto() {
        stopLive();
        if (els.live) els.live.checked = false;
        stopAuto();
        const tick = () => {
            const vals = {};
            runtime.tagList().forEach((t) => { vals[t] = Math.round(Math.random() * 100); });
            runtime.setTags(vals);
            syncRows(vals);
        };
        tick();
        autoTimer = setInterval(tick, 1500);
    }

    function pollLive() {
        fetch(tagsUrl, {
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        }).then((r) => r.json()).then((d) => {
            if (!d || !d.ok) return;
            const vals = d.values || {};
            Object.keys(vals).forEach((t) => runtime.setTag(t, vals[t]));
            syncRows(vals);
        }).catch(() => { /* ag hatasi: sessiz gec, sonraki poll dener */ });
    }

    function startLive() {
        stopAuto();
        if (els.auto) els.auto.checked = false;
        stopLive();
        pollLive();
        liveTimer = setInterval(pollLive, 4000);
    }

    function start() {
        document.body.classList.add("sim-mode");
        runtime.reset();
        runtime.start();
        buildRows();
        if (els.panel) els.panel.classList.add("show");
        if (els.btn) { els.btn.classList.add("on"); els.btn.title = "Simülasyonu durdur"; }
        onStart();
    }

    function stop() {
        stopAuto();
        stopLive();
        if (els.auto) els.auto.checked = false;
        if (els.live) els.live.checked = false;
        runtime.stop();
        document.body.classList.remove("sim-mode");
        if (els.panel) els.panel.classList.remove("show");
        if (els.btn) { els.btn.classList.remove("on"); els.btn.title = "Simülasyonu başlat"; }
        onStop();
    }

    if (els.btn) {
        els.btn.addEventListener("click", () => {
            if (runtime.isRunning()) stop(); else start();
        });
    }
    if (els.auto) {
        els.auto.addEventListener("change", () => {
            if (!runtime.isRunning()) { els.auto.checked = false; toast("Önce simülasyonu başlatın.", "warning"); return; }
            if (els.auto.checked) startAuto(); else stopAuto();
        });
    }
    if (els.live) {
        els.live.addEventListener("change", () => {
            if (!runtime.isRunning()) { els.live.checked = false; toast("Önce simülasyonu başlatın.", "warning"); return; }
            if (els.live.checked) startLive(); else stopLive();
        });
    }

    return {
        start: start,
        stop: stop,
        isRunning() { return runtime.isRunning(); },
        rebuild: buildRows,
        dispose() { stopAuto(); stopLive(); },
    };
}
