/**
 * mimic3d/boot.js — 3B mimik editorunun giris noktasi ve kontrolcusu (ESM).
 *
 * Sorumluluk: modulleri kurmak ve DOM'a baglamak. Is mantigi modullerde:
 *   core/  scene, doc, scene_io, factory, dispose, selection, history, camera_ops, render_loop
 *   edit/  transform (gizmo), ops (hizala/grupla/kopyala/sil)
 *   io/    persist (kaydet/ac/JSON), screenshot (PNG/thumbnail), gltf (GLB)
 *   ui/    inspector, outliner, toast
 *
 * Sablon `window.M3D_CONFIG` ile besler (bkz. editor3d.html).
 */

import * as THREE from "three";

import { makeStage } from "./core/scene.js";
import { makeRenderLoop } from "./core/render_loop.js";
import { LIMITS, defaultDoc, migrate, newId } from "./core/doc.js";
import { buildFromEntry } from "./core/factory.js";
import { clearChildren, disposeObject, disposeStage } from "./core/dispose.js";
import { docMap, entryOf, fromDoc, toDoc } from "./core/scene_io.js";
import { makeSelection } from "./core/selection.js";
import { makeHistory } from "./core/history.js";
import { makeCameraOps } from "./core/camera_ops.js";
import { makeTransform } from "./edit/transform.js";
import { makeOps } from "./edit/ops.js";
import { makePersist } from "./io/persist.js";
import { makeScreenshot } from "./io/screenshot.js";
import { exportGLB } from "./io/gltf.js";
import { makeInspector } from "./ui/inspector.js";
import { makeOutliner } from "./ui/outliner.js";
import { makePalette } from "./ui/palette.js";
import { makeSimPanel } from "./ui/sim_panel.js";
import { toast } from "./ui/toast.js";
import { Mimic3DRuntime } from "mimic3d/runtime";
// Sembol kutuphanesi: 2B ile AYNI symbolKey isim alani -> autoKind ve animasyon
// semantigi degismeden tasinir (bkz. mimic_core.js).
import { M3D_SYMBOLS, buildSymbol, symbolMeta, symbolThumbURL } from "mimic3d/symbols";

const CFG = window.M3D_CONFIG || {};

function $(id) { return document.getElementById(id); }
function on(id, ev, fn) { const el = $(id); if (el) el.addEventListener(ev, fn); }

export function boot() {
    // Yetenek kapisi _m3d_shell.html'de (klasik script) calisti.
    if (window.M3D_UNSUPPORTED) return null;
    const canvas = $("m3d-canvas");
    if (!canvas) return null;

    /* ------------------------------------------------------------------ */
    /* Sahne + dongu                                                       */
    /* ------------------------------------------------------------------ */
    const stage = makeStage(canvas, {
        onResize: (w, h) => { if (selection) selection.setSize(w, h); loop.invalidate(); },
    });

    let lowFx = false;
    try { lowFx = localStorage.getItem("mimic3d.lowfx") === "1"; } catch (e) { /* noop */ }

    const loop = makeRenderLoop(() => {
        const moving = stage.controls.update();
        if (moving) loop.requestContinuous("orbit");
        else loop.releaseContinuous("orbit");
        // Runtime yalniz ZAMAN-BAZLI animasyon varken surekli frame ister
        // (deger-eslemeli sahne: poll basina tek frame).
        if (runtime && runtime.needsFrame()) loop.requestContinuous("runtime");
        else loop.releaseContinuous("runtime");

        // Outline composer YALNIZ secim varken ve dusuk-efekt kapaliyken;
        // aksi halde duz render (bedava performans).
        if (!lowFx && selection && selection.count() && selection.composer) {
            selection.composer.render();
        } else {
            stage.renderer.render(stage.scene, stage.camera);
        }
    });

    /* ------------------------------------------------------------------ */
    /* Belge durumu                                                        */
    /* ------------------------------------------------------------------ */
    let baseDoc = defaultDoc();        // sahne/kamera disi alanlar (viewpoints, paths)
    let dirty = false;
    let runtime = null;                // asagida kurulur (loop ondan once tanimli)

    function markDirty() {
        if (!dirty) { dirty = true; document.body.classList.add("dirty"); }
    }
    function clearDirty() {
        dirty = false;
        document.body.classList.remove("dirty");
    }

    const factoryCtx = {
        renderer: stage.renderer,
        buildSymbol: buildSymbol,
    };

    function currentDoc() {
        return toDoc(stage, baseDoc);
    }

    /* ------------------------------------------------------------------ */
    /* Secim + gizmo                                                       */
    /* ------------------------------------------------------------------ */
    const selection = makeSelection(stage, {
        onChange: () => {
            transform.refresh();
            inspector.sync();
            refreshOutliner();
            updateStatus();
        },
    });

    const transform = makeTransform(stage, selection, loop, {
        onDragStart: () => pushUndo(),
        onChange: () => { markDirty(); inspector.sync(); },
        onDragEnd: () => { updateStatus(); },
        onModeChange: (m) => {
            ["translate", "rotate", "scale"].forEach((k) => {
                const b = $("m3d-mode-" + k);
                if (b) b.classList.toggle("on", k === m);
            });
        },
    });

    const memClipboard = { value: null };
    const ops = makeOps(stage, selection, {
        factoryCtx: factoryCtx,
        memClipboard: memClipboard,
        // Yeniden ebeveynleme yapan islemler once gizmo pivotunu cozmeli.
        releasePivot: () => transform.release(),
    });
    const cam = makeCameraOps(stage, loop);
    const shots = makeScreenshot(stage, loop);

    /* ------------------------------------------------------------------ */
    /* Gecmis (undo/redo)                                                  */
    /* ------------------------------------------------------------------ */
    const history = makeHistory({
        snapshot: () => JSON.stringify(currentDoc()),
        restore: (json) => {
            const doc = JSON.parse(json);
            applyDoc(doc, { keepCamera: true, silent: true });
        },
        onChange: () => {
            const u = $("m3d-undo"), r = $("m3d-redo");
            if (u) u.classList.toggle("disabled", !history.canUndo());
            if (r) r.classList.toggle("disabled", !history.canRedo());
        },
    });
    function pushUndo() { history.push(); }

    /* ------------------------------------------------------------------ */
    /* Belge yukleme                                                       */
    /* ------------------------------------------------------------------ */
    function applyDoc(doc, opt) {
        const o = opt || {};
        // Secili nesnelerin id'lerini sakla: undo sonrasi secim KORUNUR
        // (2B editorde olmayan bir konfor; id'ler snapshot'lar arasi sabit).
        const keepIds = selection.list().map((x) => x.userData.docId);
        transform.release();
        selection.clear();

        baseDoc = Object.assign(defaultDoc(), {
            viewpoints: doc.viewpoints || [],
            paths: doc.paths || [],
        });
        stage.applySceneSettings(doc.scene);
        if (!o.keepCamera) stage.applyCameraSettings(doc.camera);

        const res = fromDoc(stage, doc, factoryCtx);
        if (res.warnings.length && !o.silent) {
            res.warnings.slice(0, 3).forEach((w) => toast(w, "warning"));
        }
        // Secimi id ile geri kur
        if (keepIds.length) {
            const map = docMap(stage.contentRoot);
            selection.set(keepIds.map((id) => map.get(id)).filter(Boolean));
        }
        syncSceneInputs();
        refreshOutliner();
        inspector.sync();
        updateStatus();
        loop.invalidate();
        return res;
    }

    /**
     * Nesneyi YERINDE yeniden uret (geometri/etiket/buton parametresi degisti).
     * Three.js'te geometri parametreleri sonradan degistirilemez; transform,
     * id, ad, scada ve hiyerarsi konumu korunarak yeni nesne kurulur.
     */
    function rebuildObject(obj, changes) {
        const parent = obj.parent || stage.contentRoot;
        const index = parent.children.indexOf(obj);
        const entry = entryOf(obj, null);
        Object.assign(entry, changes || {});
        const kids = obj.children.filter((c) => c.userData && c.userData.docId);

        const next = buildFromEntry(entry, factoryCtx);
        if (!next) return obj;
        kids.forEach((c) => next.add(c));      // cocuklari tasi (dispose'dan once)
        disposeObject(obj);
        parent.add(next);
        if (index >= 0 && index < parent.children.length - 1) {
            parent.children.splice(parent.children.indexOf(next), 1);
            parent.children.splice(index, 0, next);
        }
        const wasSelected = selection.has(obj);
        if (wasSelected) {
            const list = selection.list().map((x) => (x === obj ? next : x));
            selection.set(list);
        }
        markDirty();
        refreshOutliner();
        loop.invalidate();
        return next;
    }

    /* ------------------------------------------------------------------ */
    /* Nesne ekleme                                                        */
    /* ------------------------------------------------------------------ */
    /** Kameranin baktigi zemin noktasi (yeni nesneler oraya birakilir). */
    function dropPoint() {
        const t = stage.controls.target.clone();
        const step = transform.snap().step || 0.25;
        return new THREE.Vector3(
            Math.round(t.x / step) * step, 0, Math.round(t.z / step) * step);
    }

    function addEntry(entry, opts2) {
        const n = docMap(stage.contentRoot).size;
        if (n >= LIMITS.maxObjects) {
            toast("Nesne sayısı üst sınıra ulaştı (" + LIMITS.maxObjects + ").", "danger");
            return null;
        }
        const e = Object.assign({ id: newId((entry.type || "o")[0]) }, entry);
        const obj = buildFromEntry(e, factoryCtx);
        if (!obj) return null;
        const p = (opts2 && opts2.at) || dropPoint();
        // Taban y=0: primitiflerde geometri merkezi kaldirma miktari kadar yukselt.
        const lift = obj.userData && obj.userData.baseLift ? obj.userData.baseLift : 0;
        obj.position.set(p.x, p.y + lift, p.z);
        stage.contentRoot.add(obj);
        selection.set([obj]);
        markDirty();
        pushUndo();
        refreshOutliner();
        updateStatus();
        loop.invalidate();
        return obj;
    }

    const PRIM_PRESETS = {
        box: { w: 1, h: 1, d: 1 },
        sphere: { r: 0.6 },
        cylinder: { r: 0.5, h: 1.4 },
        cone: { r: 0.6, h: 1.4 },
        torus: { r: 0.7 },
        plane: { w: 4, d: 4 },
        pipe: { r: 0.5, len: 3 },
        arrow: { r: 0.5, len: 1.4 },
    };

    function addPrimitive(prim) {
        return addEntry({
            type: "primitive", prim: prim, name: primName(prim),
            geo: PRIM_PRESETS[prim] || {},
            material: { color: "#8fa3b8", metalness: 0.2, roughness: 0.6 },
        });
    }
    function primName(p) {
        return ({
            box: "Kutu", sphere: "Küre", cylinder: "Silindir", cone: "Koni",
            torus: "Halka", plane: "Düzlem", pipe: "Boru", arrow: "Ok",
        })[p] || p;
    }
    /**
     * Sembol ekle. `at` verilmezse kameranin baktigi zemin noktasina birakilir
     * (surukle-birak zemin isiniyla nokta verir).
     */
    function addSymbol(key, at) {
        const meta = symbolMeta(key);
        return addEntry({
            type: "symbol", symbolKey: key, name: (meta && meta.name) || key,
            // Semboller icin varsayilan `auto`: autoKind sembole gore dogru
            // animasyonu (water/spin/flow/tint/gauge...) kendisi secer.
            scada: { anim: "auto" },
        }, at ? { at: at } : null);
    }

    function addLabel() {
        const o = addEntry({
            type: "label", name: "Etiket",
            text: { value: "0.00", planeW: 1.6, planeH: 0.45, billboard: true },
            scada: { anim: "text", decimals: 2 },
        });
        if (o) { o.position.y = 1.2; loop.invalidate(); }
        return o;
    }
    function addButton() {
        const o = addEntry({
            type: "button", name: "Buton",
            button: { label: "BAŞLAT", bg: "#2fb574", fg: "#ffffff" },
            scada: { action: "toggle", tag: "" },
        });
        if (o) { o.position.y = 0.9; loop.invalidate(); }
        return o;
    }

    /* ------------------------------------------------------------------ */
    /* Fare: secim + surukleme esigi                                       */
    /* ------------------------------------------------------------------ */
    let downPt = null;
    canvas.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        downPt = { x: e.clientX, y: e.clientY };
    });
    canvas.addEventListener("pointerup", (e) => {
        if (e.button !== 0 || !downPt) { downPt = null; return; }
        const dx = Math.abs(e.clientX - downPt.x), dy = Math.abs(e.clientY - downPt.y);
        downPt = null;
        // Kamera dondurmeyi secim sanma (2B'deki 6 px esigiyle ayni fikir).
        if (dx > 5 || dy > 5) return;
        if (transform.isDragging()) return;
        const hit = selection.pick(e.clientX, e.clientY);
        const additive = e.ctrlKey || e.metaKey || e.shiftKey;
        if (!hit) { if (!additive) selection.clear(); return; }
        if (additive) selection.toggle(hit.object);
        else selection.set([hit.object]);
    });
    canvas.addEventListener("dblclick", (e) => {
        const hit = selection.pick(e.clientX, e.clientY);
        if (hit) cam.focusPoint(hit.point);
    });

    /* ------------------------------------------------------------------ */
    /* Outliner + durum cubugu                                             */
    /* ------------------------------------------------------------------ */
    const outliner = makeOutliner($("m3d-outliner"), {
        onRefresh: () => refreshOutliner(),
        onSelect: (id, additive) => {
            const o = docMap(stage.contentRoot).get(id);
            if (!o) return;
            if (additive) selection.toggle(o); else selection.set([o]);
        },
        onToggleVisible: (id) => {
            const o = docMap(stage.contentRoot).get(id);
            if (!o) return;
            o.visible = o.visible === false;
            markDirty(); pushUndo(); refreshOutliner(); loop.invalidate();
        },
        onToggleLock: (id) => {
            const o = docMap(stage.contentRoot).get(id);
            if (!o) return;
            o.userData.locked = !o.userData.locked;
            if (o.userData.locked) selection.remove(o);
            markDirty(); pushUndo(); refreshOutliner();
        },
        onRename: (id, name) => {
            const o = docMap(stage.contentRoot).get(id);
            if (!o) return;
            o.name = name;
            markDirty(); pushUndo(); refreshOutliner(); inspector.sync();
        },
    });
    function refreshOutliner() { outliner.refresh(stage.contentRoot, selection); }

    // Sembol paleti — ikonlar gercek 3B render'dan uretilir (sessionStorage cache).
    const palette = makePalette($("m3d-palette"), $("m3d-palette-search"), {
        symbols: M3D_SYMBOLS,
        thumb: (key) => {
            try { return symbolThumbURL(key, stage.renderer, 96); }
            catch (e) { return ""; }
        },
        onAdd: (key) => addSymbol(key, null),
    });
    palette.bindCanvasDrop(canvas, (key, cx, cy) => {
        // Birakma noktasi: zemin duzlemine isin + snap adimina yuvarla.
        const p = selection.pickGround(cx, cy);
        const step = transform.snap().step || 0.25;
        const at = p
            ? new THREE.Vector3(Math.round(p.x / step) * step, 0, Math.round(p.z / step) * step)
            : null;
        addSymbol(key, at);
    });

    const inspector = makeInspector(stage, selection, {
        invalidate: () => loop.invalidate(),
        markDirty: markDirty,
        pushUndo: pushUndo,
        rebuild: rebuildObject,
        onChanged: () => refreshOutliner(),
    });

    function updateStatus() {
        const info = stage.renderer.info;
        const n = docMap(stage.contentRoot).size;
        const set = (id, v) => { const e = $(id); if (e) e.textContent = String(v); };
        set("m3d-count", n);
        set("m3d-sel", selection.count());
        set("m3d-fps", loop.fps);
        set("m3d-geo", info.memory.geometries + " / " + info.memory.textures);
        set("m3d-calls", info.render.calls);
        set("m3d-undo-depth", history.depth());
        const cEl = $("m3d-count");
        if (cEl) cEl.classList.toggle("warn", n >= LIMITS.warnObjects);
        const bEl = $("m3d-bytes");
        if (bEl) {
            const kb = Math.round(JSON.stringify(currentDoc()).length / 1024);
            bEl.textContent = kb + " KB";
            bEl.classList.toggle("warn", kb > 1500);
        }
    }
    setInterval(() => {
        const set = (id, v) => { const e = $(id); if (e) e.textContent = String(v); };
        set("m3d-fps", loop.fps);
        set("m3d-geo", stage.renderer.info.memory.geometries + " / "
            + stage.renderer.info.memory.textures);
        set("m3d-calls", stage.renderer.info.render.calls);
    }, 1000);

    /* ------------------------------------------------------------------ */
    /* Sahne sekmesi girdileri                                             */
    /* ------------------------------------------------------------------ */
    function syncSceneInputs() {
        const s = stage.sceneSettings;
        const set = (id, v) => {
            const e = $(id);
            if (!e || document.activeElement === e) return;
            if (e.type === "checkbox") e.checked = !!v; else e.value = v;
        };
        set("sc-bg-mode", s.background.mode);
        set("sc-bg-top", s.background.top);
        set("sc-bg-bottom", s.background.bottom);
        set("sc-bg-color", s.background.color);
        set("sc-grid-visible", s.grid.visible);
        set("sc-grid-size", s.grid.size);
        set("sc-ground-visible", s.ground.visible);
        set("sc-ground-color", s.ground.color);
        set("sc-shadow-quality", s.shadows.quality);
        set("sc-amb", s.lights.ambient.intensity);
        set("sc-dir", s.lights.dir.intensity);
        set("sc-exposure", s.exposure);
        set("sc-env", s.environment.preset);
        set("sc-fog", s.fog.enabled);
    }

    function bindScene(id, apply) {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", () => {
            const s = JSON.parse(JSON.stringify(stage.sceneSettings));
            apply(s, el);
            stage.applySceneSettings(s);
            markDirty();
            loop.invalidate();
            updateStatus();
        });
        el.addEventListener("change", () => pushUndo());
    }
    bindScene("sc-bg-mode", (s, el) => { s.background.mode = el.value; });
    bindScene("sc-bg-top", (s, el) => { s.background.top = el.value; });
    bindScene("sc-bg-bottom", (s, el) => { s.background.bottom = el.value; });
    bindScene("sc-bg-color", (s, el) => { s.background.color = el.value; });
    bindScene("sc-grid-visible", (s, el) => { s.grid.visible = el.checked; });
    bindScene("sc-grid-size", (s, el) => {
        const v = Math.max(4, Math.min(200, parseFloat(el.value) || 40));
        s.grid.size = v; s.grid.divisions = Math.round(v); s.ground.size = v;
    });
    bindScene("sc-ground-visible", (s, el) => { s.ground.visible = el.checked; });
    bindScene("sc-ground-color", (s, el) => { s.ground.color = el.value; });
    bindScene("sc-shadow-quality", (s, el) => {
        s.shadows.quality = el.value;
        s.shadows.enabled = el.value !== "off";
    });
    bindScene("sc-amb", (s, el) => { s.lights.ambient.intensity = parseFloat(el.value) || 0; });
    bindScene("sc-dir", (s, el) => { s.lights.dir.intensity = parseFloat(el.value) || 0; });
    bindScene("sc-exposure", (s, el) => { s.exposure = parseFloat(el.value) || 1; });
    bindScene("sc-env", (s, el) => { s.environment.preset = el.value; });
    bindScene("sc-fog", (s, el) => { s.fog.enabled = el.checked; });

    /* ------------------------------------------------------------------ */
    /* Kalicilik                                                           */
    /* ------------------------------------------------------------------ */
    const persist = makePersist(CFG, {
        getDoc: currentDoc,
        getName: () => ($("m3d-name") ? $("m3d-name").value : CFG.name),
        getDesc: () => ($("m3d-desc") ? $("m3d-desc").value : ""),
        thumbnail: () => shots.thumbnail(),
        loadDoc: (data, screen) => {
            const res = migrate(data);
            if (res.error === "2d-document") {
                toast("Bu kayıt 2B mimik belgesi — 3B editörde açılamaz.", "danger");
                return;
            }
            if (screen && screen.name && $("m3d-name")) $("m3d-name").value = screen.name;
            applyDoc(res.doc, {});
            history.reset();
            clearDirty();
            cam.fitAll();
        },
        toast: toast,
        clearDirty: clearDirty,
        width: 1280,
        height: 720,
    });

    /* ------------------------------------------------------------------ */
    /* Serit (ribbon) baglamalari                                          */
    /* ------------------------------------------------------------------ */
    document.querySelectorAll("[data-add-prim]").forEach((b) => {
        b.addEventListener("click", () => addPrimitive(b.dataset.addPrim));
    });
    on("m3d-add-label", "click", addLabel);
    on("m3d-add-button", "click", addButton);

    on("m3d-undo", "click", () => { if (!history.undo()) toast("Geri alınacak adım yok.", "warning"); });
    on("m3d-redo", "click", () => { if (!history.redo()) toast("Yeniden yapılacak adım yok.", "warning"); });
    on("m3d-duplicate", "click", () => {
        if (ops.duplicate(transform.snap().step)) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); loop.invalidate(); }
    });
    on("m3d-delete", "click", () => {
        const n = ops.remove();
        if (n) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); loop.invalidate(); }
    });
    on("m3d-copy", "click", () => {
        const n = ops.copy();
        if (n) toast(n + " nesne kopyalandı.", "success");
    });
    on("m3d-paste", "click", () => {
        const n = ops.paste(transform.snap().step);
        if (n) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); loop.invalidate(); }
        else toast("Panoda yapıştırılacak nesne yok.", "warning");
    });
    on("m3d-group", "click", () => {
        if (ops.group()) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); }
        else toast("Gruplamak için en az 2 nesne seçin.", "warning");
    });
    on("m3d-ungroup", "click", () => {
        if (ops.ungroup()) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); }
        else toast("Çözülecek grup seçili değil.", "warning");
    });
    on("m3d-ground", "click", () => {
        if (ops.dropToGround()) { markDirty(); pushUndo(); loop.invalidate(); }
    });
    on("m3d-surface", "click", () => {
        if (ops.snapToSurface()) { markDirty(); pushUndo(); loop.invalidate(); }
    });
    on("m3d-rot180", "click", () => {
        if (ops.rotate180("y")) { markDirty(); pushUndo(); loop.invalidate(); }
    });
    document.querySelectorAll("[data-align]").forEach((b) => {
        b.addEventListener("click", () => {
            const [axis, mode] = b.dataset.align.split(":");
            if (ops.align(axis, mode)) { markDirty(); pushUndo(); loop.invalidate(); }
        });
    });
    document.querySelectorAll("[data-distribute]").forEach((b) => {
        b.addEventListener("click", () => {
            if (ops.distribute(b.dataset.distribute)) { markDirty(); pushUndo(); loop.invalidate(); }
            else toast("Dağıtmak için en az 3 nesne seçin.", "warning");
        });
    });
    ["translate", "rotate", "scale"].forEach((m) => {
        on("m3d-mode-" + m, "click", () => transform.setMode(m));
    });
    on("m3d-space", "click", () => {
        const s = transform.toggleSpace();
        const b = $("m3d-space");
        if (b) b.textContent = s === "world" ? "Dünya" : "Yerel";
    });
    on("m3d-snap", "change", () => {
        const st = transform.setSnap($("m3d-snap").checked, null);
        toast("Kilitleme " + (st.on ? "açık" : "kapalı") + " (" + st.step + " m)", "success", 1500);
    });
    on("m3d-snap-step", "change", () => transform.setSnap(null, parseFloat($("m3d-snap-step").value)));

    on("m3d-save", "click", () => persist.save(false));
    on("m3d-saveas", "click", () => persist.save(true));
    on("m3d-new", "click", () => {
        if (dirty && !window.confirm("Kaydedilmemiş değişiklikler var. Yeni sahne açılsın mı?")) return;
        persist.screenId = null;
        if ($("m3d-name")) $("m3d-name").value = "Adsız 3B Sahne";
        applyDoc(defaultDoc(), {});
        history.reset();
        clearDirty();
        cam.fitAll();
    });
    on("m3d-export-png", "click", () => {
        const r = shots.exportPNG(1280, 720, 2);
        const a = document.createElement("a");
        a.href = r.url;
        a.download = (($("m3d-name") && $("m3d-name").value) || "sahne").replace(/[^\w\-]+/g, "_") + ".png";
        a.click();
        if (r.clamped) toast("Çözünürlük GPU sınırına göre küçültüldü.", "warning");
    });
    on("m3d-export-glb", "click", () => {
        exportGLB(stage, (($("m3d-name") && $("m3d-name").value) || "sahne").replace(/[^\w\-]+/g, "_"),
                  () => toast("GLB dışa aktarıldı.", "success"),
                  () => toast("GLB dışa aktarma hatası.", "danger"));
    });
    on("m3d-export-json", "click", () => persist.exportJSON());
    on("m3d-import-json", "click", () => { const f = $("m3d-file-json"); if (f) f.click(); });
    on("m3d-file-json", "change", (e) => {
        const f = e.target.files && e.target.files[0];
        if (f) {
            persist.importJSON(f).then((ok) => {
                if (ok) { history.reset(); markDirty(); cam.fitAll(); }
            });
        }
        e.target.value = "";
    });

    // Ac diyalogu
    on("m3d-open", "click", () => {
        const modal = $("m3d-open-modal");
        const list = $("m3d-open-list");
        if (!modal || !list) return;
        list.innerHTML = '<div class="ol-empty">Yükleniyor…</div>';
        modal.classList.add("show");
        persist.list().then((items) => {
            if (!items.length) {
                list.innerHTML = '<div class="ol-empty">Kayıtlı 3B sahne yok.</div>';
                return;
            }
            list.innerHTML = items.map((m) => (
                '<div class="op-card" data-id="' + m.id + '">'
                + (m.thumbnail ? '<img src="' + m.thumbnail + '" alt="">' : '<div class="op-noimg">3B</div>')
                + '<div class="op-meta"><b>' + String(m.name).replace(/</g, "&lt;") + "</b>"
                + "<span>" + m.updated_at + "</span></div></div>"
            )).join("");
            list.querySelectorAll(".op-card").forEach((c) => {
                c.addEventListener("click", () => {
                    modal.classList.remove("show");
                    persist.open(c.dataset.id);
                });
            });
        });
    });
    on("m3d-open-close", "click", () => $("m3d-open-modal").classList.remove("show"));

    // Gorunum kontrolleri
    document.querySelectorAll("[data-view]").forEach((b) => {
        b.addEventListener("click", () => cam.preset(b.dataset.view));
    });
    on("m3d-fit", "click", () => cam.fitAll());
    on("m3d-fit-sel", "click", () => {
        if (selection.count()) cam.fitObjects(selection.list());
        else cam.fitAll();
    });
    on("m3d-toggle-grid", "click", () => {
        const s = JSON.parse(JSON.stringify(stage.sceneSettings));
        s.grid.visible = !s.grid.visible;
        stage.applySceneSettings(s);
        const b = $("m3d-toggle-grid");
        if (b) b.classList.toggle("on", s.grid.visible);
        syncSceneInputs();
        markDirty();
        loop.invalidate();
    });
    on("m3d-lowfx", "click", () => {
        lowFx = !lowFx;
        try { localStorage.setItem("mimic3d.lowfx", lowFx ? "1" : "0"); } catch (e) { /* noop */ }
        const s = JSON.parse(JSON.stringify(stage.sceneSettings));
        s.shadows.enabled = !lowFx && s.shadows.quality !== "off";
        stage.applySceneSettings(s);
        const b = $("m3d-lowfx");
        if (b) b.classList.toggle("on", lowFx);
        toast("Düşük efekt modu " + (lowFx ? "açık" : "kapalı"), "success", 1600);
        loop.invalidate();
    });

    // Sag panel sekmeleri
    document.querySelectorAll(".rp-tab").forEach((t) => {
        t.addEventListener("click", () => {
            document.querySelectorAll(".rp-tab").forEach((x) => x.classList.remove("active"));
            document.querySelectorAll(".rp-pane").forEach((x) => x.classList.remove("active"));
            t.classList.add("active");
            const pane = $(t.dataset.pane);
            if (pane) pane.classList.add("active");
        });
    });

    // Tema
    on("m3d-theme", "click", () => {
        const cur = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-bs-theme", cur);
        try { localStorage.setItem("data-bs-theme", cur); } catch (e) { /* noop */ }
    });

    /* ------------------------------------------------------------------ */
    /* Klavye                                                              */
    /* ------------------------------------------------------------------ */
    function isTyping() {
        const a = document.activeElement;
        return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT"
            || a.isContentEditable);
    }
    document.addEventListener("keydown", (e) => {
        if (isTyping()) return;
        const ctrl = e.ctrlKey || e.metaKey;
        const k = e.key.toLowerCase();

        if (ctrl && k === "z" && !e.shiftKey) { e.preventDefault(); history.undo(); return; }
        if (ctrl && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); history.redo(); return; }
        if (ctrl && k === "s") { e.preventDefault(); persist.save(false); return; }
        if (ctrl && k === "c") { e.preventDefault(); ops.copy(); return; }
        if (ctrl && k === "v") {
            e.preventDefault();
            if (ops.paste(transform.snap().step)) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); loop.invalidate(); }
            return;
        }
        if (ctrl && k === "d") {
            e.preventDefault();
            if (ops.duplicate(transform.snap().step)) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); loop.invalidate(); }
            return;
        }
        if (ctrl && k === "g") {
            e.preventDefault();
            if (e.shiftKey) ops.ungroup(); else ops.group();
            markDirty(); pushUndo(); refreshOutliner(); updateStatus();
            return;
        }
        if (ctrl && k === "a") {
            e.preventDefault();
            selection.set(stage.contentRoot.children.filter((c) => c.userData && c.userData.docId));
            return;
        }
        if (k === "delete" || k === "backspace") {
            e.preventDefault();
            if (ops.remove()) { markDirty(); pushUndo(); refreshOutliner(); updateStatus(); loop.invalidate(); }
            return;
        }
        if (k === "escape") { selection.clear(); return; }
        // Gizmo modlari (Blender/Unity yaygin kisayollari)
        if (k === "w") { transform.setMode("translate"); return; }
        if (k === "e") { transform.setMode("rotate"); return; }
        if (k === "r") { transform.setMode("scale"); return; }
        if (k === "x") {
            const s = transform.toggleSpace();
            const b = $("m3d-space");
            if (b) b.textContent = s === "world" ? "Dünya" : "Yerel";
            return;
        }
        if (k === "f") { if (selection.count()) cam.fitObjects(selection.list()); return; }
        if (k === "g") {
            const btn = $("m3d-toggle-grid");
            if (btn) btn.click();
            return;
        }
        if (k === "l") { const b = $("m3d-lowfx"); if (b) b.click(); return; }
        if (k >= "1" && k <= "9") {
            const vp = (baseDoc.viewpoints || [])[parseInt(k, 10) - 1];
            if (vp) cam.flyTo(vp);
            return;
        }
        // Ok tuslariyla kaydirma. NOT: 3B'de W/E/R gizmo moduna ayrildigi icin
        // 2B'deki WASD kaydirma YOKTUR (bilincli sapma, dokumantasyonda).
        const step = transform.snap().step * (e.shiftKey ? 4 : 1);
        let axis = null, amt = 0;
        if (k === "arrowleft") { axis = "x"; amt = -step; }
        else if (k === "arrowright") { axis = "x"; amt = step; }
        else if (k === "arrowup") { axis = e.altKey ? "y" : "z"; amt = e.altKey ? step : -step; }
        else if (k === "arrowdown") { axis = e.altKey ? "y" : "z"; amt = e.altKey ? -step : step; }
        if (axis && selection.count()) {
            e.preventDefault();
            ops.nudge(axis, amt);
            markDirty();
            inspector.sync();
            loop.invalidate();
            debouncedNudgeUndo();
        }
    });
    let nudgeTimer = null;
    function debouncedNudgeUndo() {
        if (nudgeTimer) clearTimeout(nudgeTimer);
        nudgeTimer = setTimeout(() => { nudgeTimer = null; pushUndo(); }, 450);
    }

    /* ------------------------------------------------------------------ */
    /* WebGL context + kapanis                                             */
    /* ------------------------------------------------------------------ */
    canvas.addEventListener("webglcontextlost", (ev) => {
        ev.preventDefault();
        loop.stop();
        toast("Grafik bağlamı kayboldu. Sayfayı yenileyin.", "danger", 12000);
    }, false);
    canvas.addEventListener("webglcontextrestored", () => {
        toast("Grafik bağlamı geri geldi.", "success");
        loop.start();
    }, false);

    window.addEventListener("beforeunload", (e) => {
        if (dirty) {
            e.preventDefault();
            e.returnValue = "";
            return "";
        }
        // Temiz cikista GPU kaynaklarini bosalt.
        try { selection.dispose(); transform.dispose(); disposeStage(stage); } catch (x) { /* noop */ }
        return undefined;
    });

    window.addEventListener("resize", () => stage.resize());

    /* ------------------------------------------------------------------ */
    /* Simulasyon (runtime)                                                */
    /* ------------------------------------------------------------------ */
    runtime = Mimic3DRuntime({
        root: stage.contentRoot,
        invalidate: () => loop.invalidate(),
        camera: stage.camera,
        controls: stage.controls,
        // cameraTour + pathFollow belgedeki gorus noktalari/yollari kullanir.
        get viewpoints() { return baseDoc.viewpoints || []; },
        get paths() { return baseDoc.paths || []; },
    });

    const simPanel = makeSimPanel({
        panel: $("m3d-sim-panel"),
        tags: $("m3d-sim-tags"),
        auto: $("m3d-sim-auto"),
        live: $("m3d-sim-live"),
        btn: $("m3d-sim"),
    }, {
        runtime: runtime,
        tagsUrl: CFG.urls.tags,
        toast: toast,
        onStart: () => {
            // Simulasyonda duzenleme kapali: gizmo gizlenir, secim temizlenir.
            selection.clear();
            loop.invalidate();
        },
        onStop: () => { refreshOutliner(); inspector.sync(); loop.invalidate(); },
    });

    // Simulasyonda butona tiklamak etiketi surer (2B'deki press/release).
    canvas.addEventListener("pointerdown", (e) => {
        if (!runtime.isRunning() || e.button !== 0) return;
        const hit = selection.pick(e.clientX, e.clientY);
        if (hit && hit.object.userData.docType === "button") {
            runtime.pressButton(hit.object);
            canvas.__pressed = hit.object;
        }
    });
    canvas.addEventListener("pointerup", () => {
        if (canvas.__pressed) {
            runtime.releaseButton(canvas.__pressed);
            canvas.__pressed = null;
        }
    });

    /* ------------------------------------------------------------------ */
    /* Gercek sensor etiketleri + mimik listesi                             */
    /* ------------------------------------------------------------------ */
    // Etiket bağlama alanı gerçek `Sensor.tag` degerleriyle beslenir (2B
    // editordeki datalist ile ayni sozlesme: api_mimic_tags).
    const TAGMETA = {};
    function loadTags() {
        fetch(CFG.urls.tags, {
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        }).then((r) => r.json()).then((d) => {
            if (!d || !d.ok) return;
            const dl = $("m3d-tag-options");
            if (dl) {
                dl.innerHTML = (d.results || []).map((it) => {
                    const lbl = (it.label || "") + (it.unit ? " (" + it.unit + ")" : "")
                        + (it.station ? " · " + it.station : "");
                    return '<option value="' + String(it.tag).replace(/"/g, "&quot;")
                        + '">' + String(lbl).replace(/</g, "&lt;") + "</option>";
                }).join("");
            }
            (d.results || []).forEach((it) => { TAGMETA[it.tag] = it; });
        }).catch(() => { /* etiketsiz de calisir */ });
    }
    loadTags();

    // "Başka Mimik Aç" hedef dropdown'u — 2B ve 3B mimiklerin TAMAMI (capraz
    // gezinme icin; viewer route'u türe göre doğru şablonu seçer).
    function loadMimicList() {
        const sel = $("ib-link-target");
        if (!sel) return;
        persist.listAll().then((items) => {
            sel.innerHTML = '<option value="">—</option>' + items.map((m) => (
                '<option value="' + m.id + '">' + String(m.name).replace(/</g, "&lt;")
                + (m.kind === "3d" ? " (3B)" : " (2B)") + "</option>"
            )).join("");
            inspector.sync();
        }).catch(() => { /* noop */ });
    }
    loadMimicList();

    /* ------------------------------------------------------------------ */
    /* Baslangic                                                           */
    /* ------------------------------------------------------------------ */
    const raw = (CFG.initial && CFG.initial.data) || null;
    const res = migrate(raw);
    if (res.error === "2d-document") {
        toast("Bu kayıt 2B mimik belgesi — 3B editörde açılamaz.", "danger", 9000);
        applyDoc(defaultDoc(), {});
    } else {
        applyDoc(res.doc, {});
        res.warnings.forEach((w) => toast(w, "warning"));
    }
    history.reset();
    clearDirty();
    stage.resize();
    if (!res.doc.objects || !res.doc.objects.length) cam.fitAll();
    transform.setMode("translate");
    loop.start();
    loop.invalidate();

    // Teshis/test kancasi.
    window.__m3d = {
        THREE, stage, loop, selection, transform, ops, cam, history, persist,
        shots, inspector, outliner, palette, toast, runtime, simPanel,
        symbols: { M3D_SYMBOLS, buildSymbol, symbolMeta },
        currentDoc, applyDoc, rebuildObject, addEntry, addPrimitive, addLabel, addButton, addSymbol,
        docMap: () => docMap(stage.contentRoot),
        isDirty: () => dirty,
        markDirty, pushUndo, updateStatus, refreshOutliner,
        factoryCtx,
    };
    return window.__m3d;
}

boot();
