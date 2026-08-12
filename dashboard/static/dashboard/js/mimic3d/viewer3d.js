/**
 * mimic3d/viewer3d.js — 3B mimik goruntuleyici (salt-okunur, HER ZAMAN CANLI).
 *
 * 2B viewer'in (viewer.html) 3B ikizi. Simulasyon/otomatik/canli SECIMI YOKTUR:
 * gercek HMI deneyimi icin acilista `api_mimic_tags`'i 4 sn'de bir poll'lar ve
 * animasyonlari GERCEK sensor degerleriyle surer.
 *
 * ETKILESIM KATMANI PAYLASILIR: tiklama menusu, rapor modali (DataTables +
 * ApexCharts), kontrol diyalogu ve "baska mimik ac" mantigi `mimic_menu_ui.js`
 * modulunden gelir — 2B viewer ile AYNI. Iki viewer YALNIZ "tiklanan nesneyi
 * nasil buldugu" ile ayrisir: 2B Fabric event'i, 3B raycaster.
 *
 * `mimic_viewer` route'u TEK oldugu ve sablonu `MimicScreen.kind` sectigi icin
 * 2B<->3B capraz `openMimic` gezinmesi ek kod GEREKTIRMEZ.
 */

import * as THREE from "three";

import { makeStage } from "./core/scene.js";
import { makeRenderLoop } from "./core/render_loop.js";
import { migrate } from "./core/doc.js";
import { fromDoc } from "./core/scene_io.js";
import { buildSymbol } from "mimic3d/symbols";
import { Mimic3DRuntime } from "mimic3d/runtime";

const CFG = window.V3D_CFG || {};

function $(id) { return document.getElementById(id); }

export function boot() {
    if (window.M3D_UNSUPPORTED) return null;
    const canvas = $("v3d-canvas");
    if (!canvas) return null;

    const I18N = CFG.i18n || {};
    const stage = makeStage(canvas, { onResize: () => loop.invalidate() });
    // Viewer POSTPROCESSING YUKLEMEZ (EffectComposer/OutlinePass import edilmez):
    // secim vurgusu gerekmiyor, mutevazi saha PC'sinde bedava performans.
    const loop = makeRenderLoop(() => {
        const moving = stage.controls.update();
        if (moving) loop.requestContinuous("orbit");
        else loop.releaseContinuous("orbit");
        if (runtime && runtime.needsFrame()) loop.requestContinuous("runtime");
        else loop.releaseContinuous("runtime");
        stage.renderer.render(stage.scene, stage.camera);
    });

    /* ------------------------------------------------------------------ */
    /* Belge                                                               */
    /* ------------------------------------------------------------------ */
    const raw = (CFG.initial && CFG.initial.data) || null;
    const res = migrate(raw);
    let doc = res.doc;
    if (res.error === "2d-document") {
        const w = $("v3d-warn");
        if (w) { w.textContent = I18N.doc2d || "Bu kayıt 2B mimik belgesi."; w.style.display = "block"; }
    }
    stage.applySceneSettings(doc.scene);
    stage.applyCameraSettings(doc.camera);
    fromDoc(stage, doc, { renderer: stage.renderer, buildSymbol: buildSymbol });

    /* ------------------------------------------------------------------ */
    /* Runtime (her zaman calisir)                                         */
    /* ------------------------------------------------------------------ */
    const runtime = Mimic3DRuntime({
        root: stage.contentRoot,
        invalidate: () => loop.invalidate(),
        camera: stage.camera,
        controls: stage.controls,
        get viewpoints() { return doc.viewpoints || []; },
        get paths() { return doc.paths || []; },
    });
    runtime.start();

    /* ------------------------------------------------------------------ */
    /* Etiket meta verisi + paylasilan etkilesim katmani                    */
    /* ------------------------------------------------------------------ */
    const TAGMETA = {};
    const menuUI = window.MimicMenuUI.init({
        reportDataUrl: CFG.reportDataUrl,
        controlUrl: CFG.controlUrl,
        viewerTpl: CFG.viewerTpl,
        csrf: CFG.csrf,
        canControl: CFG.canControl,
        getMeta: (tag) => TAGMETA[tag] || {},
    });

    /* ------------------------------------------------------------------ */
    /* Tiklama: raycast + surukleme esigi                                   */
    /* ------------------------------------------------------------------ */
    const raycaster = new THREE.Raycaster();
    const ndc = new THREE.Vector2();

    function docAncestor(o) {
        let n = o;
        while (n) {
            if (n.userData && n.userData.docId) return n;
            n = n.parent;
        }
        return null;
    }

    function pick(clientX, clientY) {
        const rect = canvas.getBoundingClientRect();
        ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
        ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(ndc, stage.camera);
        const hits = raycaster.intersectObjects(stage.contentRoot.children, true);
        for (let i = 0; i < hits.length; i++) {
            const t = docAncestor(hits[i].object);
            if (t && t.visible !== false) return t;
        }
        return null;
    }

    /** Bir nesne "etkilesimli" mi: buton VEYA (etiketi + menusu etkin). */
    function interactive(o) {
        if (!o) return false;
        if (o.userData.docType === "button") return true;
        const sc = o.scada || {};
        return !!(sc.menu && sc.menu.enabled && sc.tag);
    }

    let down = null;
    let pressed = null;
    canvas.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        down = { x: e.clientX, y: e.clientY, t: performance.now() };
        const hit = pick(e.clientX, e.clientY);
        if (hit && hit.userData.docType === "button") {
            const sc = hit.scada || {};
            if ((sc.action || "") !== "openMimic") {
                runtime.pressButton(hit);
                pressed = hit;
            }
        }
    });
    canvas.addEventListener("pointerup", (e) => {
        if (pressed) { runtime.releaseButton(pressed); pressed = null; }
        if (!down) return;
        const dx = Math.abs(e.clientX - down.x), dy = Math.abs(e.clientY - down.y);
        const dt = performance.now() - down.t;
        const isTouch = e.pointerType === "touch";
        // Kamera dondurmeyi tiklama sanma. Dokunmatikte esik daha genis
        // (parmak titremesi) + sure siniri (uzun basma = kaydirma).
        const moveOk = isTouch ? (dx < 10 && dy < 10 && dt < 300) : (dx < 6 && dy < 6);
        down = null;
        if (!moveOk) return;
        const hit = pick(e.clientX, e.clientY);
        if (!hit) { menuUI.hideMenu(); return; }
        const sc = hit.scada || {};
        if (hit.userData.docType === "button" && (sc.action || "") === "openMimic") {
            menuUI.openMimicLink(sc);
            return;
        }
        if (hit.userData.docType === "button") return;   // buton menu acmaz
        if (interactive(hit)) menuUI.openMenu(sc, e.clientX, e.clientY);
    });

    // Etkilesimli nesnelerin uzerinde imleci degistir (kesfedilebilirlik).
    let hoverRaf = 0;
    canvas.addEventListener("pointermove", (e) => {
        if (hoverRaf) return;
        hoverRaf = requestAnimationFrame(() => {
            hoverRaf = 0;
            const hit = pick(e.clientX, e.clientY);
            canvas.style.cursor = interactive(hit) ? "pointer" : "";
        });
    });

    /* ------------------------------------------------------------------ */
    /* Canli veri (4 sn)                                                    */
    /* ------------------------------------------------------------------ */
    const ind = $("v3d-ind");
    const indTxt = $("v3d-ind-txt");
    function setInd(state, txt) {
        if (ind) ind.className = "v-ind " + state;
        if (indTxt) indTxt.textContent = txt;
    }
    function pollLive() {
        fetch(CFG.tagsUrl, {
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        }).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then((d) => {
                const vals = (d && d.values) || {};
                Object.keys(vals).forEach((t) => runtime.setTag(t, vals[t]));
                ((d && d.results) || []).forEach((it) => { TAGMETA[it.tag] = it; });
                setInd("ok", I18N.live || "Canlı");
                menuUI.refreshCurrent();
                loop.invalidate();
            })
            .catch(() => setInd("err", I18N.offline || "Bağlantı yok"));
    }
    pollLive();
    setInterval(pollLive, 4000);

    /* ------------------------------------------------------------------ */
    /* Saat + ust bar                                                       */
    /* ------------------------------------------------------------------ */
    const LOCALE = CFG.locale === "en" ? "en-US" : "tr-TR";
    function clockStr() {
        try {
            return new Date().toLocaleTimeString(LOCALE,
                { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        } catch (e) { return ""; }
    }
    function tickClock() { const c = $("v3d-clock"); if (c) c.textContent = clockStr(); }
    tickClock();
    setInterval(tickClock, 1000);

    /* --- gorunum kontrolleri --- */
    function fitAll() {
        const box = new THREE.Box3();
        stage.contentRoot.children.forEach((o) => box.expandByObject(o));
        if (box.isEmpty()) {
            const s = (stage.sceneSettings.grid && stage.sceneSettings.grid.size) || 40;
            box.set(new THREE.Vector3(-s / 2, 0, -s / 2), new THREE.Vector3(s / 2, s * 0.2, s / 2));
        }
        const c = box.getCenter(new THREE.Vector3());
        const r = Math.max(0.5, box.getSize(new THREE.Vector3()).length() * 0.5);
        const dist = (r / Math.sin((stage.camera.fov * Math.PI) / 360)) * 1.25;
        const dir = new THREE.Vector3(1, 0.62, 1.15).normalize();
        stage.camera.position.copy(c).add(dir.multiplyScalar(dist));
        stage.controls.target.copy(c);
        stage.controls.maxDistance = Math.max(stage.controls.maxDistance, dist * 3);
        stage.controls.update();
        loop.invalidate();
    }
    const btnFit = $("v3d-fit");
    if (btnFit) btnFit.addEventListener("click", fitAll);

    // Kayitli gorus noktalari -> ust barda dugmeler (3B'ye ozgu HMI konforu)
    const vpHost = $("v3d-viewpoints");
    if (vpHost && (doc.viewpoints || []).length) {
        vpHost.innerHTML = doc.viewpoints.map((v, i) => (
            '<button class="v-btn" data-vp="' + i + '">' + String(v.name || ("Görüş " + (i + 1)))
                .replace(/</g, "&lt;") + "</button>"
        )).join("");
        vpHost.querySelectorAll("[data-vp]").forEach((b) => {
            b.addEventListener("click", () => {
                const vp = doc.viewpoints[parseInt(b.dataset.vp, 10)];
                if (!vp) return;
                stage.camera.position.fromArray(vp.position || [10, 8, 12]);
                stage.controls.target.fromArray(vp.target || [0, 1, 0]);
                if (vp.fov) { stage.camera.fov = vp.fov; stage.camera.updateProjectionMatrix(); }
                stage.controls.update();
                loop.invalidate();
            });
        });
    }

    /* --- tam ekran --- */
    function fsActive() { return document.fullscreenElement || document.webkitFullscreenElement; }
    const btnFull = $("v3d-full");
    if (btnFull) {
        btnFull.addEventListener("click", () => {
            const el = document.documentElement;
            if (!fsActive()) (el.requestFullscreen || el.webkitRequestFullscreen).call(el);
            else (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        });
    }
    document.addEventListener("fullscreenchange", () => {
        setTimeout(() => stage.resize(), 60);
    });

    /* --- tema (chrome temaya uyar, SAHNE UYMAZ) --- */
    const btnTheme = $("v3d-theme");
    if (btnTheme) {
        btnTheme.addEventListener("click", () => {
            const cur = document.documentElement.getAttribute("data-bs-theme") === "dark"
                ? "light" : "dark";
            document.documentElement.setAttribute("data-bs-theme", cur);
            try { localStorage.setItem("data-bs-theme", cur); } catch (e) { /* noop */ }
        });
    }

    canvas.addEventListener("webglcontextlost", (ev) => {
        ev.preventDefault();
        loop.stop();
        setInd("err", I18N.glLost || "Grafik bağlamı kayboldu");
    }, false);

    window.addEventListener("resize", () => stage.resize());
    stage.resize();
    loop.start();
    if (!doc.objects || !doc.objects.length) fitAll();

    window.__v3d = {
        THREE, stage, loop, runtime, menuUI, pick, interactive, fitAll,
        tagMeta: () => TAGMETA,
        pollLive,
    };
    return window.__v3d;
}

boot();
