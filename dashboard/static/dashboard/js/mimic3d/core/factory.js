/**
 * mimic3d/core/factory.js — belge girdisi -> THREE.Object3D uretimi.
 *
 * Uretilen her nesne su sozlesmeye uyar:
 *   userData.docId    belge id'si (secim/animasyon/outliner bu id ile eslesir)
 *   userData.docType  "primitive" | "label" | "button" | "group" | "symbol" | "sceneBinding"
 *   userData.symbolKey (yalniz symbol) — autoKind icin
 *   .scada            baglama blogu (2B ile ayni sema)
 *
 * Birim METRE, Y yukari; primitifler ve etiketler **tabani y=0'da** uretilir ki
 * izgaraya birakma (drop) sembollerle ayni davransin.
 */

import * as THREE from "three";

import { defaultScada } from "./doc.js";

/* ------------------------------------------------------------------ */
/* Etiket (canvas texture)                                            */
/* ------------------------------------------------------------------ */
/**
 * NEDEN canvas-texture (CSS2DRenderer degil):
 *  - CSS2DRenderer DOM'a cizer -> `renderer.domElement.toDataURL()` onu GORMEZ.
 *    PNG export ve galeri thumbnail'i icin TEK mekanizma bu oldugundan tum
 *    deger gostergeleri onizlemelerden kaybolurdu.
 *  - DOM overlay geometriyle derinlik testine girmez (tankin arkasindaki etiket
 *    onunde gorunur) ve raycast edilemez -> viewer'in tiklama menusu calismaz.
 *  - TextGeometry ise ek font varligi ister ve her deger degisiminde geometri
 *    yeniden kurar (4 sn'de bir geometri cop uretimi).
 * Canvas texture uc sorunu da cozer: framebuffer'in ICINDE, gercek Object3D,
 * guncelleme maliyeti = kucuk bir 2D cizim + tek texture upload.
 */
const LABEL_DEFAULTS = {
    value: "0", font: "Inter, Arial, sans-serif", size: 128, weight: 700,
    color: "#e8f0f8", bg: "#0b1017", bgOpacity: 0.55, align: "center",
    padding: 18, billboard: true, planeW: 1.6, planeH: 0.45, dpi: 256,
};

function nextPow2(v) {
    return Math.pow(2, Math.ceil(Math.log2(Math.max(2, v))));
}

function drawLabelCanvas(canvas, spec, scale) {
    const s = Object.assign({}, LABEL_DEFAULTS, spec || {});
    const mult = scale || 1;
    const w = Math.min(2048, nextPow2(s.dpi * s.planeW * mult));
    const h = Math.min(1024, nextPow2(s.dpi * s.planeH * mult));
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    if (s.bgOpacity > 0) {
        ctx.globalAlpha = s.bgOpacity;
        ctx.fillStyle = s.bg;
        const r = Math.min(w, h) * 0.12;
        ctx.beginPath();
        ctx.moveTo(r, 0);
        ctx.lineTo(w - r, 0); ctx.quadraticCurveTo(w, 0, w, r);
        ctx.lineTo(w, h - r); ctx.quadraticCurveTo(w, h, w - r, h);
        ctx.lineTo(r, h); ctx.quadraticCurveTo(0, h, 0, h - r);
        ctx.lineTo(0, r); ctx.quadraticCurveTo(0, 0, r, 0);
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
    }

    const lines = String(s.value == null ? "" : s.value).split("\n");
    // Yaziyi kutuya sigdir: once istenen punto, tasarsa kucult.
    let fontPx = Math.min(h * 0.62, s.size * mult * (h / (s.dpi * s.planeH * mult)));
    ctx.textBaseline = "middle";
    for (let guard = 0; guard < 24; guard++) {
        ctx.font = s.weight + " " + fontPx + "px " + s.font;
        const maxW = Math.max.apply(null, lines.map((l) => ctx.measureText(l).width));
        if (maxW <= w - s.padding * 2 * mult || fontPx <= 8) break;
        fontPx *= 0.9;
    }
    ctx.fillStyle = s.color;
    ctx.textAlign = s.align === "left" ? "left" : (s.align === "right" ? "right" : "center");
    const x = s.align === "left" ? s.padding * mult
        : (s.align === "right" ? w - s.padding * mult : w / 2);
    const lh = fontPx * 1.18;
    const y0 = h / 2 - ((lines.length - 1) * lh) / 2;
    lines.forEach((l, i) => ctx.fillText(l, x, y0 + i * lh));
    return canvas;
}

/** Etiket olustur — `billboard` ise Sprite, degilse duzlem mesh. */
export function makeLabel(spec, renderer) {
    const s = Object.assign({}, LABEL_DEFAULTS, spec || {});
    const canvas = document.createElement("canvas");
    drawLabelCanvas(canvas, s, 1);

    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.generateMipmaps = true;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    // Egik acilarda yaziyi bulanmaktan kurtaran sey budur.
    if (renderer) tex.anisotropy = renderer.capabilities.getMaxAnisotropy();

    let obj;
    if (s.billboard) {
        // Sprite: her zaman kameraya doner, per-frame is yok.
        const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
        obj = new THREE.Sprite(mat);
        obj.scale.set(s.planeW, s.planeH, 1);
    } else {
        // MeshBasic (Standard DEGIL): gosterge isik yonunden bagimsiz okunmali,
        // isik rigi degisince sonmemeli.
        const mat = new THREE.MeshBasicMaterial({
            map: tex, transparent: true, depthWrite: false, side: THREE.DoubleSide,
        });
        obj = new THREE.Mesh(new THREE.PlaneGeometry(s.planeW, s.planeH), mat);
    }
    obj.userData.labelCanvas = canvas;
    obj.userData.labelSpec = s;
    obj.userData.labelTex = tex;
    obj.userData.lastText = String(s.value);
    return obj;
}

/**
 * Etiket metnini degistir. Metin AYNIYSA hicbir sey yapmaz -> sabit bir dijital
 * etikette 4 sn'lik poll SIFIR texture upload'i demektir.
 * @returns {boolean} texture guncellendi mi
 */
export function setLabelText(obj, text) {
    if (!obj || !obj.userData || !obj.userData.labelCanvas) return false;
    const str = String(text == null ? "" : text);
    if (obj.userData.lastText === str) return false;
    obj.userData.lastText = str;
    const spec = Object.assign({}, obj.userData.labelSpec, { value: str });
    obj.userData.labelSpec = spec;
    drawLabelCanvas(obj.userData.labelCanvas, spec, 1);
    obj.userData.labelTex.needsUpdate = true;
    return true;
}

/** PNG export'ta etiketleri `mult` katinda yeniden ciz (upscale bulanikligi yerine). */
export function rescaleLabel(obj, mult) {
    if (!obj || !obj.userData || !obj.userData.labelCanvas) return;
    drawLabelCanvas(obj.userData.labelCanvas, obj.userData.labelSpec, mult);
    obj.userData.labelTex.needsUpdate = true;
}

/* ------------------------------------------------------------------ */
/* HMI butonu                                                         */
/* ------------------------------------------------------------------ */
const BUTTON_DEFAULTS = {
    label: "BUTON", bg: "#2fb574", fg: "#ffffff",
    w: 1.2, h: 0.42, d: 0.10, radius: 0.06, fontsize: 110,
};

export function makeButton3D(spec, renderer) {
    const s = Object.assign({}, BUTTON_DEFAULTS, spec || {});
    const g = new THREE.Group();
    const face = new THREE.Mesh(
        new THREE.BoxGeometry(s.w, s.h, s.d),
        new THREE.MeshStandardMaterial({ color: s.bg, metalness: 0.1, roughness: 0.45 }));
    face.userData.role = "buttonFace";
    face.userData.tintable = true;
    face.castShadow = true;
    g.add(face);

    const txt = makeLabel({
        value: s.label, planeW: s.w * 0.92, planeH: s.h * 0.72, size: s.fontsize,
        color: s.fg, bgOpacity: 0, billboard: false, dpi: 320,
    }, renderer);
    txt.position.z = s.d / 2 + 0.002;
    txt.userData.role = "label";
    g.add(txt);

    g.userData.buttonSpec = s;
    return g;
}

/* ------------------------------------------------------------------ */
/* Primitifler                                                        */
/* ------------------------------------------------------------------ */
const GEO_DEFAULTS = { w: 1, h: 1, d: 1, r: 0.5, seg: 24, len: 2, radius: 0 };

/** Bir primitif geometri uret. Taban y=0 olacak sekilde merkezi ofsetlenir. */
function primGeometry(prim, geo) {
    const g = Object.assign({}, GEO_DEFAULTS, geo || {});
    switch (prim) {
        case "sphere":
            return { geom: new THREE.SphereGeometry(g.r, Math.max(8, g.seg), Math.max(6, Math.round(g.seg / 2))), lift: g.r };
        case "cylinder":
            return { geom: new THREE.CylinderGeometry(g.r, g.r, g.h, Math.max(6, g.seg)), lift: g.h / 2 };
        case "cone":
            return { geom: new THREE.ConeGeometry(g.r, g.h, Math.max(6, g.seg)), lift: g.h / 2 };
        case "torus":
            return { geom: new THREE.TorusGeometry(g.r, Math.max(0.02, g.r * 0.28), 14, Math.max(10, g.seg)), lift: g.r };
        case "plane": {
            const geom = new THREE.PlaneGeometry(g.w, g.d);
            geom.rotateX(-Math.PI / 2);   // yatay duzlem
            return { geom: geom, lift: 0 };
        }
        case "pipe": {
            // Yatay boru: silindiri Z eksenine yatir.
            const geom = new THREE.CylinderGeometry(g.r * 0.5, g.r * 0.5, g.len, Math.max(8, g.seg));
            geom.rotateZ(Math.PI / 2);
            return { geom: geom, lift: g.r * 0.5 };
        }
        case "arrow": {
            // Ok: govde + koni, tek geometriye birlestirilmeden grup olarak
            // donmesi gerekirdi; basit tutmak icin koni kullanip yon veriyoruz.
            const geom = new THREE.ConeGeometry(g.r * 0.5, g.len, 16);
            geom.rotateZ(-Math.PI / 2);
            return { geom: geom, lift: g.r * 0.5 };
        }
        case "box":
        default:
            return { geom: new THREE.BoxGeometry(g.w, g.h, g.d), lift: g.h / 2 };
    }
}

export function makePrimitive(prim, geo, material) {
    const { geom, lift } = primGeometry(prim, geo);
    const m = material || {};
    const mat = new THREE.MeshStandardMaterial({
        color: m.color || "#8fa3b8",
        metalness: m.metalness == null ? 0.2 : Number(m.metalness),
        roughness: m.roughness == null ? 0.6 : Number(m.roughness),
        transparent: !!m.transparent || (m.opacity != null && Number(m.opacity) < 1),
        opacity: m.opacity == null ? 1 : Number(m.opacity),
        wireframe: !!m.wireframe,
        flatShading: !!m.flatShading,
        side: m.side === "double" ? THREE.DoubleSide : THREE.FrontSide,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.userData.tintable = true;
    mesh.userData.baseLift = lift;    // taban y=0 icin gereken kaldirma
    return mesh;
}

/* ------------------------------------------------------------------ */
/* Belge girdisi -> nesne                                             */
/* ------------------------------------------------------------------ */

/**
 * @param {object} entry belge nesnesi
 * @param {object} ctx   {renderer, buildSymbol?}  buildSymbol P4'te gelir
 * @returns {THREE.Object3D|null} bilinmeyen tipte null (cagiran uyari basar)
 */
export function buildFromEntry(entry, ctx) {
    const c = ctx || {};
    let obj = null;
    switch (entry.type) {
        case "group":
            obj = new THREE.Group();
            break;
        case "primitive":
            obj = makePrimitive(entry.prim || "box", entry.geo, entry.material);
            // Serialize geri yazabilsin diye uretim parametrelerini sakla
            // (geometriden geri cikarmak kayipli olurdu).
            obj.userData.prim = entry.prim || "box";
            obj.userData.geo = Object.assign({}, GEO_DEFAULTS, entry.geo || {});
            break;
        case "label":
            obj = makeLabel(entry.text, c.renderer);
            break;
        case "button":
            obj = makeButton3D(entry.button, c.renderer);
            break;
        case "symbol":
            if (c.buildSymbol) obj = c.buildSymbol(entry.symbolKey);
            if (!obj) {
                // Sembol kutuphanesi yoksa/anahtar taninmiyorsa yer tutucu:
                // sahne ACILMALI, tek bir bilinmeyen sembol yuzunden coküp
                // kullanicinin tasarimini kaybetmemeli.
                obj = makePrimitive("box", { w: 0.8, h: 0.8, d: 0.8 },
                                    { color: "#7a879a", roughness: 0.8 });
                obj.userData.placeholder = true;
            }
            obj.userData.symbolKey = entry.symbolKey || "";
            break;
        case "sceneBinding":
            // Gorunmez tasiyici: sahne seviyesi animasyonlar (cameraTour) icin.
            obj = new THREE.Object3D();
            obj.visible = false;
            break;
        default:
            return null;   // "import" (GLB) ve gelecekteki tipler
    }

    obj.userData.docId = entry.id;
    obj.userData.docType = entry.type;
    obj.name = entry.name || entry.type;
    if (Array.isArray(entry.position)) obj.position.fromArray(entry.position);
    if (Array.isArray(entry.rotation)) obj.rotation.fromArray(entry.rotation);
    if (Array.isArray(entry.scale)) obj.scale.fromArray(entry.scale);
    obj.visible = entry.visible !== false;
    obj.userData.locked = !!entry.locked;
    if (entry.castShadow != null || entry.receiveShadow != null) {
        obj.traverse((o) => {
            if (!o.isMesh) return;
            if (entry.castShadow != null) o.castShadow = !!entry.castShadow;
            if (entry.receiveShadow != null) o.receiveShadow = !!entry.receiveShadow;
        });
    }
    obj.scada = Object.assign(defaultScada(), entry.scada || {});
    return obj;
}
