/**
 * mimic3d/ui/inspector.js — sag panel: Özellik / Animasyon / Sahne sekmeleri.
 *
 * 2B editordeki `syncProps` + `bindScada` desenini izler:
 *   - `sync()` secimden alanlara yazar (odakli input EZILMEZ),
 *   - alan degisimi secime uygulanir + dirty isaretlenir,
 *   - hangi bolumlerin gorunecegine body class'lari karar verir
 *     (`sel-primitive`, `sel-label`, `sel-button`, `sel-symbol`, `has-selection`).
 *
 * Undo: transform/geometri gibi surekli alanlar 400 ms debounce ile tek
 * snapshot uretir (her tus vurusunda undo adimi olusmaz).
 */

import * as THREE from "three";

import { ensureOwnMaterials } from "../core/dispose.js";
import { makeLabel, setLabelText, makePrimitive } from "../core/factory.js";

function $(id) { return document.getElementById(id); }

const NUM = (v, d) => {
    const n = parseFloat(v);
    return isNaN(n) ? d : n;
};

export function makeInspector(stage, selection, opts) {
    const options = opts || {};
    const invalidate = options.invalidate || function () {};
    const markDirty = options.markDirty || function () {};
    const pushUndo = options.pushUndo || function () {};
    const rebuild = options.rebuild || function () {};   // nesneyi yeniden uret (geometri degisimi)
    let syncing = false;
    let undoTimer = null;

    function debouncedUndo() {
        if (undoTimer) clearTimeout(undoTimer);
        undoTimer = setTimeout(() => { undoTimer = null; pushUndo(); }, 400);
    }

    function first() { return selection.first(); }

    /* ---------------------------------------------------------------- */
    /* Alan tanimlari                                                    */
    /* ---------------------------------------------------------------- */
    // [inputId, oku(obj), yaz(obj, deger)]
    const RAD = Math.PI / 180;
    const FIELDS = [
        ["ip-x", (o) => o.position.x, (o, v) => { o.position.x = v; }],
        ["ip-y", (o) => o.position.y, (o, v) => { o.position.y = v; }],
        ["ip-z", (o) => o.position.z, (o, v) => { o.position.z = v; }],
        ["ip-rx", (o) => o.rotation.x / RAD, (o, v) => { o.rotation.x = v * RAD; }],
        ["ip-ry", (o) => o.rotation.y / RAD, (o, v) => { o.rotation.y = v * RAD; }],
        ["ip-rz", (o) => o.rotation.z / RAD, (o, v) => { o.rotation.z = v * RAD; }],
        ["ip-sx", (o) => o.scale.x, (o, v) => { o.scale.x = v || 0.001; }],
        ["ip-sy", (o) => o.scale.y, (o, v) => { o.scale.y = v || 0.001; }],
        ["ip-sz", (o) => o.scale.z, (o, v) => { o.scale.z = v || 0.001; }],
    ];

    // Geometri parametreleri: degisince mesh YENIDEN URETILIR (geometry
    // parametreleri sonradan degistirilemez).
    const GEO_FIELDS = ["ig-w", "ig-h", "ig-d", "ig-r", "ig-seg", "ig-len"];
    const GEO_KEYS = { "ig-w": "w", "ig-h": "h", "ig-d": "d", "ig-r": "r", "ig-seg": "seg", "ig-len": "len" };

    const SCADA_FIELDS = [
        ["b-tag", "tag", "str"], ["b-expr", "expr", "str"], ["b-anim", "anim", "str"],
        ["b-axis", "axis", "str"],
        ["b-min", "min", "num"], ["b-max", "max", "num"], ["b-threshold", "threshold", "num"],
        ["b-speed", "speed", "num"], ["b-moverange", "moveRange", "num"],
        ["b-oncolor", "onColor", "str"], ["b-offcolor", "offColor", "str"],
        ["b-unit", "unit", "str"], ["b-decimals", "decimals", "num"],
    ];
    const MENU_KEYS = ["enabled", "historic", "report", "daily", "control"];

    /* ---------------------------------------------------------------- */
    /* Senkron: secim -> panel                                           */
    /* ---------------------------------------------------------------- */
    function setVal(id, v) {
        const el = $(id);
        if (!el) return;
        if (document.activeElement === el) return;    // kullanici yaziyor: EZME
        if (el.type === "checkbox") el.checked = !!v;
        else el.value = (v == null ? "" : (typeof v === "number" ? Math.round(v * 1e4) / 1e4 : v));
    }

    function sync() {
        syncing = true;
        const o = first();
        const n = selection.count();
        const body = document.body.classList;
        body.toggle("has-selection", n > 0);
        ["primitive", "label", "button", "symbol", "group", "sceneBinding"].forEach((t) => {
            body.toggle("sel-" + t, !!o && o.userData.docType === t);
        });
        body.toggle("multi-selection", n > 1);

        const info = $("ip-info");
        if (info) {
            info.textContent = n === 0 ? "Seçim yok"
                : (n === 1 ? (o.name || o.userData.docType) : n + " nesne seçili");
        }
        if (!o) { syncing = false; return; }

        setVal("ip-name", o.name);
        FIELDS.forEach(([id, get]) => setVal(id, get(o)));

        if (o.userData.docType === "primitive") {
            const g = o.userData.geo || {};
            GEO_FIELDS.forEach((id) => setVal(id, g[GEO_KEYS[id]]));
            setVal("ip-prim", o.userData.prim || "box");
            const m = o.material;
            if (m) {
                setVal("im-color", "#" + m.color.getHexString());
                setVal("im-metal", m.metalness);
                setVal("im-rough", m.roughness);
                setVal("im-opacity", m.opacity);
                setVal("im-wire", m.wireframe);
            }
        } else if (o.userData.docType === "symbol") {
            setVal("ip-symbolkey", o.userData.symbolKey || "");
            const m = o.material && !Array.isArray(o.material) ? o.material : null;
            if (m) setVal("im-color", "#" + m.color.getHexString());
        } else if (o.userData.docType === "label") {
            const s = o.userData.labelSpec || {};
            setVal("il-text", s.value);
            setVal("il-size", s.size);
            setVal("il-color", s.color);
            setVal("il-bg", s.bg);
            setVal("il-bgop", s.bgOpacity);
            setVal("il-w", s.planeW);
            setVal("il-h", s.planeH);
            setVal("il-billboard", s.billboard);
        } else if (o.userData.docType === "button") {
            const s = o.userData.buttonSpec || {};
            setVal("ib-label", s.label);
            setVal("ib-bg", s.bg);
            setVal("ib-fg", s.fg);
            setVal("ib-w", s.w);
            setVal("ib-h", s.h);
            setVal("ib-action", (o.scada && o.scada.action) || "toggle");
            setVal("ib-setvalue", (o.scada && o.scada.setValue) == null ? 100 : o.scada.setValue);
            setVal("ib-link-target", (o.scada && o.scada.linkTarget) || "");
            setVal("ib-link-mode", (o.scada && o.scada.linkMode) || "modal");
        }

        // Animasyon sekmesi
        const sc = o.scada || {};
        SCADA_FIELDS.forEach(([id, key]) => setVal(id, sc[key]));
        setVal("b-showunit", sc.showUnit !== false);
        const mn = sc.menu || {};
        MENU_KEYS.forEach((k) => setVal("b-menu-" + k, !!mn[k]));

        syncing = false;
    }

    /* ---------------------------------------------------------------- */
    /* Baglama: panel -> secim                                           */
    /* ---------------------------------------------------------------- */
    function applyAll(fn, undo) {
        const items = selection.list();
        if (!items.length) return;
        items.forEach(fn);
        markDirty();
        invalidate();
        if (options.onChanged) options.onChanged();
        if (undo !== false) debouncedUndo();
    }

    function bindNum(id, setter) {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", () => {
            if (syncing) return;
            applyAll((o) => setter(o, NUM(el.value, 0)));
        });
    }

    FIELDS.forEach(([id, , set]) => bindNum(id, set));

    // Ad
    const nameEl = $("ip-name");
    if (nameEl) {
        nameEl.addEventListener("input", () => {
            if (syncing) return;
            const o = first();
            if (!o) return;
            o.name = nameEl.value;
            markDirty();
            if (options.onChanged) options.onChanged();
            debouncedUndo();
        });
    }

    // Geometri: yeniden uretim gerektirir
    GEO_FIELDS.forEach((id) => {
        const el = $(id);
        if (!el) return;
        el.addEventListener("change", () => {
            if (syncing) return;
            const o = first();
            if (!o || o.userData.docType !== "primitive") return;
            const geo = Object.assign({}, o.userData.geo);
            geo[GEO_KEYS[id]] = NUM(el.value, geo[GEO_KEYS[id]]);
            rebuild(o, { geo: geo });
            debouncedUndo();
        });
    });
    const primEl = $("ip-prim");
    if (primEl) {
        primEl.addEventListener("change", () => {
            if (syncing) return;
            const o = first();
            if (!o || o.userData.docType !== "primitive") return;
            rebuild(o, { prim: primEl.value });
            debouncedUndo();
        });
    }

    // Material
    function bindMat(id, apply) {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", () => {
            if (syncing) return;
            applyAll((o) => {
                // Paylasilan material'i klonla: yoksa bir sembolu boyamak AYNI
                // material'i kullanan TUM sembolleri boyar.
                ensureOwnMaterials(o);
                o.traverse((m) => {
                    if (!m.isMesh || !m.material || Array.isArray(m.material)) return;
                    if (m.userData && m.userData.tintable === false) return;
                    apply(m.material, el);
                });
            });
        });
    }
    bindMat("im-color", (m, el) => m.color.set(el.value));
    bindMat("im-metal", (m, el) => { m.metalness = NUM(el.value, 0.2); });
    bindMat("im-rough", (m, el) => { m.roughness = NUM(el.value, 0.6); });
    bindMat("im-opacity", (m, el) => {
        const v = NUM(el.value, 1);
        m.opacity = v;
        // transparent bayragi acilmazsa opacity SESSIZCE yok sayilir.
        m.transparent = v < 1;
        m.needsUpdate = true;
    });
    bindMat("im-wire", (m, el) => { m.wireframe = !!el.checked; });

    // Etiket
    function bindLabel(id, key, cast) {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", () => {
            if (syncing) return;
            const o = first();
            if (!o || o.userData.docType !== "label") return;
            const v = el.type === "checkbox" ? el.checked
                : (cast === "num" ? NUM(el.value, 0) : el.value);
            const spec = Object.assign({}, o.userData.labelSpec);
            spec[key] = v;
            if (key === "value") {
                o.userData.labelSpec = spec;
                o.userData.lastText = null;      // yeniden ciz
                setLabelText(o, v);
            } else {
                rebuild(o, { text: spec });
            }
            markDirty();
            invalidate();
            if (options.onChanged) options.onChanged();
            debouncedUndo();
        });
    }
    bindLabel("il-text", "value", "str");
    bindLabel("il-size", "size", "num");
    bindLabel("il-color", "color", "str");
    bindLabel("il-bg", "bg", "str");
    bindLabel("il-bgop", "bgOpacity", "num");
    bindLabel("il-w", "planeW", "num");
    bindLabel("il-h", "planeH", "num");
    bindLabel("il-billboard", "billboard", "bool");

    // Buton
    function bindButton(id, key, cast) {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", () => {
            if (syncing) return;
            const o = first();
            if (!o || o.userData.docType !== "button") return;
            const spec = Object.assign({}, o.userData.buttonSpec);
            spec[key] = cast === "num" ? NUM(el.value, 0) : el.value;
            rebuild(o, { button: spec });
            debouncedUndo();
        });
    }
    bindButton("ib-label", "label", "str");
    bindButton("ib-bg", "bg", "str");
    bindButton("ib-fg", "fg", "str");
    bindButton("ib-w", "w", "num");
    bindButton("ib-h", "h", "num");

    ["ib-action", "ib-setvalue", "ib-link-target", "ib-link-mode"].forEach((id) => {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", () => {
            if (syncing) return;
            const o = first();
            if (!o) return;
            o.scada = o.scada || {};
            if (id === "ib-action") o.scada.action = el.value;
            else if (id === "ib-setvalue") o.scada.setValue = NUM(el.value, 100);
            else if (id === "ib-link-target") o.scada.linkTarget = el.value || null;
            else o.scada.linkMode = el.value;
            toggleLinkFields();
            markDirty();
            debouncedUndo();
        });
    });
    function toggleLinkFields() {
        const o = first();
        const isLink = !!(o && o.scada && o.scada.action === "openMimic");
        document.body.classList.toggle("btn-openmimic", isLink);
    }

    // Animasyon (scada)
    SCADA_FIELDS.forEach(([id, key, cast]) => {
        const el = $(id);
        if (!el) return;
        el.addEventListener("input", () => {
            if (syncing) return;
            applyAll((o) => {
                o.scada = o.scada || {};
                o.scada[key] = cast === "num" ? NUM(el.value, 0) : el.value;
            }, false);
            if (key === "anim") syncAnimFields();
            debouncedUndo();
        });
    });
    const showUnitEl = $("b-showunit");
    if (showUnitEl) {
        showUnitEl.addEventListener("change", () => {
            if (syncing) return;
            applyAll((o) => { o.scada = o.scada || {}; o.scada.showUnit = showUnitEl.checked; }, false);
            debouncedUndo();
        });
    }
    MENU_KEYS.forEach((k) => {
        const el = $("b-menu-" + k);
        if (!el) return;
        el.addEventListener("change", () => {
            if (syncing) return;
            applyAll((o) => {
                o.scada = o.scada || {};
                o.scada.menu = Object.assign({
                    enabled: false, historic: false, report: false, daily: false, control: false,
                }, o.scada.menu || {});
                o.scada.menu[k] = el.checked;
            }, false);
            debouncedUndo();
        });
    });

    /** Animasyon turune gore ilgisiz alanlari gizle (2B'deki desen). */
    function syncAnimFields() {
        const o = first();
        const anim = (o && o.scada && o.scada.anim) || "none";
        const b = document.body.classList;
        b.toggle("anim-axis", ["rotate", "rotateAxis", "scaleAxis", "tilt", "moveZ"].indexOf(anim) >= 0);
        b.toggle("anim-range", ["level", "opacity", "moveX", "moveY", "moveZ", "scaleAxis",
                                "emissive", "tilt", "pathFollow", "auto"].indexOf(anim) >= 0);
        b.toggle("anim-colors", ["colorState", "fillThreshold", "blink", "emissive", "auto"].indexOf(anim) >= 0);
        b.toggle("anim-text", anim === "text");
        b.toggle("anim-speed", ["rotate", "rotateAxis", "blink", "auto", "pathFollow", "cameraTour"].indexOf(anim) >= 0);
    }

    return {
        sync() { sync(); toggleLinkFields(); syncAnimFields(); },
        syncAnimFields: syncAnimFields,
    };
}
