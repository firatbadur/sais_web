/**
 * mimic3d_symbols.js — 3B SCADA sembol kutuphanesi (prosedurel geometri).
 * ---------------------------------------------------------------------------
 * 2B kutuphanesi (mimic_symbols.js, 121 SVG sembol) ile **AYNI `symbolKey`
 * isim alanini** kullanir. Bu sayede `MimicCore.autoKind` siniflandirmasi ve
 * animasyon semantigi degismeden tasinir: `tank_vertical` iki renderer'da da
 * "water", `pump_centrifugal` iki renderer'da da "spin" davranir.
 *
 * TASARIM KURALLARI (her builder uymak ZORUNDA):
 *  1. `THREE.Group` dondur; `userData.symbolKey`/`docId` cagiran tarafindan yazilir.
 *  2. Birim METRE, Y yukari, footprint X/Z merkezinde, **taban y=0** ->
 *     izgaraya birakma sembol basina ofset tablosu gerektirmez.
 *  3. Animasyonlu cocuklar `userData.role` tasir. Kapali kume:
 *     rotor · needle · liquid · liquidSurface · bubbles · flow · carousel ·
 *     spray · doorLeafL · doorLeafR · indicator · label · buttonFace
 *  4. Boyanabilir govdeler `userData.tintable = true`. Sivi/cam/akis kilifi/
 *     etiket ISARETLENMEZ — yoksa `colorState` suyu boyar.
 *  5. Rol parametreleri GRUP uzerinde: liqBottom, liqTop, spinAxis, needleAxis,
 *     needleMin, needleMax, doorTravel, tiltMin, tiltMax.
 *  6. Golge: kati mesh'ler cast, yatay ustler receive; sivi/akis/kabarcik/sprey/
 *     etiket ikisi de FALSE (saydam golge = bedava maliyet + gorsel gurultu).
 *  7. Yalniz `MAT` paylasilan material'lari kullan (hepsi userData.shared=true).
 *     Runtime tint'te `ensureOwnMaterials` klonlar (bkz. core/dispose.js).
 *
 * PALET: hex degerleri mimic_symbols.js'ten BIREBIR kopyalandi (iki editor ayni
 * urun gibi gorunsun). Bu modulde tema/CSS okumasi YOKTUR — kayitli tasarim
 * acik/koyu modda ve PNG'de AYNI gorunmelidir.
 */

import * as THREE from "three";

/* ------------------------------------------------------------------ */
/* Palet + paylasilan material'lar                                     */
/* ------------------------------------------------------------------ */
export const M3D_PALETTE = Object.freeze({
    BODY: "#c2ccd6", EDGE: "#5e6b7a", METAL: "#93a1b0", DARK: "#37414d",
    STEEL: "#aeb9c4", LIQ: "#2f9bd6", GREEN: "#3fbf6f", RED: "#e4544c",
    AMBER: "#f1b44c", WHITE: "#ffffff", GLASS: "#cfe3f0", CONCRETE: "#9aa4ae",
});

const P = M3D_PALETTE;
const _mats = {};

function mat(name, color, metalness, roughness, extra) {
    const key = name + "|" + color;
    if (_mats[key]) return _mats[key];
    const m = new THREE.MeshStandardMaterial(Object.assign({
        color: color, metalness: metalness, roughness: roughness,
    }, extra || {}));
    // Paylasilan: dispose EDILMEZ, runtime degistirmek isterse klonlar.
    m.userData.shared = true;
    _mats[key] = m;
    return m;
}

export const MAT = {
    get body() { return mat("body", P.BODY, 0.15, 0.55); },
    get edge() { return mat("edge", P.EDGE, 0.20, 0.60); },
    get metal() { return mat("metal", P.METAL, 0.85, 0.35); },
    get steel() { return mat("steel", P.STEEL, 0.70, 0.40); },
    get dark() { return mat("dark", P.DARK, 0.30, 0.70); },
    get concrete() { return mat("concrete", P.CONCRETE, 0.05, 0.90); },
    get white() { return mat("white", P.WHITE, 0.05, 0.50); },
    get liquid() {
        return mat("liquid", P.LIQ, 0.10, 0.15,
                   { transparent: true, opacity: 0.82, depthWrite: false });
    },
    get glass() {
        return mat("glass", P.GLASS, 0.0, 0.05,
                   { transparent: true, opacity: 0.25, side: THREE.DoubleSide });
    },
    get green() { return mat("green", P.GREEN, 0.10, 0.45, { emissive: P.GREEN, emissiveIntensity: 0.25 }); },
    get red() { return mat("red", P.RED, 0.10, 0.45, { emissive: P.RED, emissiveIntensity: 0.25 }); },
    get amber() { return mat("amber", P.AMBER, 0.10, 0.45, { emissive: P.AMBER, emissiveIntensity: 0.25 }); },
};

/* ------------------------------------------------------------------ */
/* Paylasilan prosedurel texture'lar (GORSEL DOSYA YOK -> offline guvenli)     */
/* ------------------------------------------------------------------ */
const _tex = {};

/** Akis kilifi icin kesikli alfa deseni (2B'deki setLineDash karsiligi). */
function dashTexture() {
    if (_tex.dash) return _tex.dash;
    const w = 16, h = 1;
    const data = new Uint8Array(w * h * 4);
    for (let i = 0; i < w; i++) {
        const on = (i % 8) < 4;
        data[i * 4 + 0] = 255; data[i * 4 + 1] = 255; data[i * 4 + 2] = 255;
        data[i * 4 + 3] = on ? 210 : 0;
    }
    const t = new THREE.DataTexture(data, w, h, THREE.RGBAFormat);
    t.wrapS = THREE.RepeatWrapping;
    t.wrapT = THREE.ClampToEdgeWrapping;
    t.magFilter = THREE.LinearFilter;
    t.needsUpdate = true;
    t.userData = { shared: true };
    _tex.dash = t;
    return t;
}

/** Su yuzeyi icin kucuk prosedurel normal map (dalga kirilmasi). */
function waterNormal() {
    if (_tex.waterN) return _tex.waterN;
    const n = 64;
    const data = new Uint8Array(n * n * 4);
    for (let y = 0; y < n; y++) {
        for (let x = 0; x < n; x++) {
            const i = (y * n + x) * 4;
            const dx = Math.sin((x / n) * Math.PI * 4) * 0.5 + Math.sin((y / n) * Math.PI * 6) * 0.3;
            const dy = Math.cos((y / n) * Math.PI * 4) * 0.5 + Math.cos((x / n) * Math.PI * 5) * 0.3;
            data[i + 0] = Math.floor(128 + dx * 60);
            data[i + 1] = Math.floor(128 + dy * 60);
            data[i + 2] = 255;
            data[i + 3] = 255;
        }
    }
    const t = new THREE.DataTexture(data, n, n, THREE.RGBAFormat);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.needsUpdate = true;
    t.userData = { shared: true };
    _tex.waterN = t;
    return t;
}

export const M3D_TEX = { dash: dashTexture, waterNormal: waterNormal };

/* ------------------------------------------------------------------ */
/* Yardimci geometri kiti (builder'lar kisa kalsin)                    */
/* ------------------------------------------------------------------ */
function mesh(geom, material, opts) {
    const m = new THREE.Mesh(geom, material);
    const o = opts || {};
    if (o.pos) m.position.set(o.pos[0], o.pos[1], o.pos[2]);
    if (o.rot) m.rotation.set(o.rot[0], o.rot[1], o.rot[2]);
    if (o.scale) m.scale.set(o.scale[0], o.scale[1], o.scale[2]);
    m.castShadow = o.cast !== false;
    m.receiveShadow = !!o.receive;
    if (o.role) m.userData.role = o.role;
    if (o.tintable) m.userData.tintable = true;
    if (o.name) m.name = o.name;
    if (o.noShadow) { m.castShadow = false; m.receiveShadow = false; }
    return m;
}
const box = (w, h, d, material, opts) => mesh(new THREE.BoxGeometry(w, h, d), material, opts);
const cyl = (rt, rb, h, material, opts, seg) =>
    mesh(new THREE.CylinderGeometry(rt, rb, h, seg || 24), material, opts);
const cone = (r, h, material, opts, seg) =>
    mesh(new THREE.ConeGeometry(r, h, seg || 24), material, opts);
const sph = (r, material, opts, seg) =>
    mesh(new THREE.SphereGeometry(r, seg || 20, Math.max(8, Math.round((seg || 20) / 2))), material, opts);
const torus = (r, t, material, opts) =>
    mesh(new THREE.TorusGeometry(r, t, 12, 24), material, opts);
const plane = (w, h, material, opts) =>
    mesh(new THREE.PlaneGeometry(w, h), material, opts);

function group(children) {
    const g = new THREE.Group();
    (children || []).forEach((c) => { if (c) g.add(c); });
    return g;
}

/** Yatay boru + akis kilifi (flow animasyonu icin). */
function pipeRun(len, radius, axis) {
    const g = new THREE.Group();
    const body = cyl(radius, radius, len, MAT.metal, {});
    if (axis === "x") body.rotation.z = Math.PI / 2;
    else if (axis === "z") body.rotation.x = Math.PI / 2;
    body.userData.tintable = true;
    g.add(body);
    // Akis kilifi: boruyla es eksenli, hafif buyuk, toplamali harmanlama.
    const sleeveMat = new THREE.MeshBasicMaterial({
        map: dashTexture(), transparent: true, blending: THREE.AdditiveBlending,
        depthWrite: false, side: THREE.DoubleSide, color: 0x66ccff,
    });
    sleeveMat.userData.shared = false;   // her boru kendi offset'ini surer
    sleeveMat.map = dashTexture().clone();
    sleeveMat.map.wrapS = THREE.RepeatWrapping;
    sleeveMat.map.repeat.set(Math.max(2, Math.round(len * 2)), 1);
    sleeveMat.map.needsUpdate = true;
    const sleeve = cyl(radius * 1.06, radius * 1.06, len * 0.96, sleeveMat, { noShadow: true });
    if (axis === "x") sleeve.rotation.z = Math.PI / 2;
    else if (axis === "z") sleeve.rotation.x = Math.PI / 2;
    sleeve.userData.role = "flow";
    sleeve.visible = false;              // runtime deger>0 olunca acar
    g.add(sleeve);
    return g;
}

/** Silindirik hazneye sivi + yuzey (water animasyonu icin). */
function liquidCylinder(g, radius, hInner, yBottom) {
    const liq = cyl(radius, radius, hInner, MAT.liquid,
                    { pos: [0, yBottom + hInner / 2, 0], noShadow: true });
    liq.userData.role = "liquid";
    liq.scale.y = 0.001;                 // baslangicta bos
    liq.position.y = yBottom;
    g.add(liq);
    const surfMat = new THREE.MeshStandardMaterial({
        color: P.LIQ, metalness: 0.1, roughness: 0.18, transparent: true, opacity: 0.9,
        normalMap: waterNormal(), normalScale: new THREE.Vector2(0.4, 0.4),
    });
    surfMat.userData.shared = false;     // her hazne kendi normal offset'ini surer
    surfMat.normalMap = waterNormal().clone();
    surfMat.normalMap.wrapS = surfMat.normalMap.wrapT = THREE.RepeatWrapping;
    surfMat.normalMap.needsUpdate = true;
    const surf = mesh(new THREE.CircleGeometry(radius * 0.985, 28), surfMat,
                      { rot: [-Math.PI / 2, 0, 0], pos: [0, yBottom, 0], noShadow: true });
    surf.userData.role = "liquidSurface";
    g.add(surf);
    g.userData.liqBottom = yBottom;
    g.userData.liqTop = yBottom + hInner;
    g.userData.liqInnerH = hInner;
    return g;
}

/** Dikdortgen hazneye sivi + yuzey. */
function liquidBox(g, w, d, hInner, yBottom) {
    const liq = box(w, hInner, d, MAT.liquid, { pos: [0, yBottom, 0], noShadow: true });
    liq.userData.role = "liquid";
    liq.scale.y = 0.001;
    g.add(liq);
    const surfMat = new THREE.MeshStandardMaterial({
        color: P.LIQ, metalness: 0.1, roughness: 0.18, transparent: true, opacity: 0.9,
        normalMap: waterNormal().clone(), normalScale: new THREE.Vector2(0.4, 0.4),
    });
    surfMat.userData.shared = false;
    surfMat.normalMap.wrapS = surfMat.normalMap.wrapT = THREE.RepeatWrapping;
    surfMat.normalMap.needsUpdate = true;
    const surf = plane(w * 0.99, d * 0.99, surfMat,
                       { rot: [-Math.PI / 2, 0, 0], pos: [0, yBottom, 0], noShadow: true });
    surf.userData.role = "liquidSurface";
    g.add(surf);
    g.userData.liqBottom = yBottom;
    g.userData.liqTop = yBottom + hInner;
    g.userData.liqInnerH = hInner;
    return g;
}

/** Yukselen kabarcik bulutu (aeration). */
function bubbles(w, d, h, count) {
    const n = count || 120;
    const pos = new Float32Array(n * 3);
    const phase = new Float32Array(n);
    for (let i = 0; i < n; i++) {
        pos[i * 3 + 0] = (Math.random() - 0.5) * w * 0.8;
        pos[i * 3 + 1] = Math.random() * h;
        pos[i * 3 + 2] = (Math.random() - 0.5) * d * 0.8;
        phase[i] = Math.random();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const m = new THREE.PointsMaterial({
        color: 0xffffff, size: 0.045, transparent: true, opacity: 0.75,
        sizeAttenuation: true, depthWrite: false,
    });
    m.userData.shared = false;
    const pts = new THREE.Points(geo, m);
    pts.userData.role = "bubbles";
    pts.userData.phase = phase;
    pts.userData.bubbleH = h;
    pts.visible = false;
    return pts;
}

/** Yikama barindan cikan sprey (washbar). */
function spray(w, count) {
    const n = count || 80;
    const pos = new Float32Array(n * 3);
    const seed = new Float32Array(n);
    for (let i = 0; i < n; i++) {
        pos[i * 3 + 0] = 0; pos[i * 3 + 1] = 0; pos[i * 3 + 2] = 0;
        seed[i] = Math.random();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const m = new THREE.PointsMaterial({
        color: 0xbfe4ff, size: 0.035, transparent: true, opacity: 0.7,
        sizeAttenuation: true, depthWrite: false,
    });
    m.userData.shared = false;
    const pts = new THREE.Points(geo, m);
    pts.userData.role = "spray";
    pts.userData.seed = seed;
    pts.userData.barW = w;
    pts.visible = false;
    return pts;
}

/** Durum lambasi/gostergesi (indicator rolu -> yalniz bu boyanir). */
function indicator(r, y, material) {
    const m = sph(r, material || MAT.green, { pos: [0, y, 0], noShadow: true });
    m.userData.role = "indicator";
    m.userData.tintable = true;
    return m;
}

/* ------------------------------------------------------------------ */
/* Sembol builder'lari                                                 */
/* ------------------------------------------------------------------ */
const B = {};   // key -> build()

/* --- VANALAR (autoKind: tint) --- */
function valveBase(handleShape) {
    const g = group();
    g.add(pipeRun(0.9, 0.11, "x"));
    const body = box(0.34, 0.30, 0.34, MAT.body, { pos: [0, 0, 0], tintable: true });
    g.add(body);
    g.add(box(0.06, 0.34, 0.34, MAT.metal, { pos: [-0.2, 0, 0] }));
    g.add(box(0.06, 0.34, 0.34, MAT.metal, { pos: [0.2, 0, 0] }));
    g.add(cyl(0.035, 0.035, 0.26, MAT.metal, { pos: [0, 0.27, 0] }));
    if (handleShape === "wheel") {
        g.add(torus(0.16, 0.025, MAT.red, { pos: [0, 0.42, 0], rot: [Math.PI / 2, 0, 0] }));
    } else if (handleShape === "lever") {
        g.add(box(0.42, 0.05, 0.07, MAT.red, { pos: [0.1, 0.42, 0] }));
    } else if (handleShape === "butterfly") {
        g.add(cyl(0.13, 0.13, 0.05, MAT.dark, { pos: [0, 0.42, 0] }));
        g.add(box(0.34, 0.04, 0.06, MAT.red, { pos: [0.08, 0.46, 0] }));
    } else if (handleShape === "solenoid") {
        g.add(box(0.22, 0.26, 0.22, MAT.dark, { pos: [0, 0.40, 0], tintable: true }));
        g.add(indicator(0.035, 0.56, MAT.green));
    }
    g.position.y = 0;
    // Taban y=0: govde merkezi 0'da oldugundan tumunu yukari kaydir.
    g.children.forEach((c) => { c.position.y += 0.17; });
    return g;
}
B.valve_gate = () => valveBase("wheel");
B.valve_ball = () => valveBase("lever");
B.valve_butterfly = () => valveBase("butterfly");
B.valve_solenoid = () => valveBase("solenoid");

/* --- POMPALAR & MOTORLAR (autoKind: spin) --- */
B.pump_centrifugal = () => {
    const g = group();
    g.add(box(0.86, 0.10, 0.52, MAT.dark, { pos: [0, 0.05, 0], receive: true }));
    // salyangoz govde
    const body = cyl(0.26, 0.26, 0.20, MAT.body, { pos: [-0.16, 0.38, 0], rot: [0, 0, Math.PI / 2], tintable: true });
    g.add(body);
    g.add(cyl(0.10, 0.10, 0.18, MAT.metal, { pos: [-0.16, 0.38, 0.20], rot: [Math.PI / 2, 0, 0] }));
    // motor
    g.add(cyl(0.15, 0.15, 0.40, MAT.steel, { pos: [0.22, 0.38, 0], rot: [0, 0, Math.PI / 2] }));
    g.add(box(0.10, 0.16, 0.16, MAT.dark, { pos: [0.44, 0.38, 0] }));
    // rotor (spin): kameraya bakan carklar
    const rotor = group([
        cyl(0.055, 0.055, 0.05, MAT.metal, { pos: [0, 0, 0], rot: [Math.PI / 2, 0, 0], noShadow: true }),
        box(0.30, 0.035, 0.02, MAT.green, { pos: [0, 0, 0], noShadow: true }),
        box(0.02, 0.035, 0.30, MAT.green, { pos: [0, 0, 0], noShadow: true }),
    ]);
    rotor.position.set(-0.16, 0.38, 0.115);
    rotor.userData.role = "rotor";
    g.add(rotor);
    g.userData.spinAxis = "z";
    return g;
};
B.pump_peristaltic = () => {
    const g = group();
    g.add(box(0.60, 0.10, 0.44, MAT.dark, { pos: [0, 0.05, 0], receive: true }));
    g.add(box(0.46, 0.46, 0.30, MAT.body, { pos: [0, 0.36, 0], tintable: true }));
    g.add(cyl(0.17, 0.17, 0.06, MAT.dark, { pos: [0, 0.36, 0.16], rot: [Math.PI / 2, 0, 0] }));
    const rotor = group([
        cyl(0.045, 0.045, 0.05, MAT.metal, { pos: [0, 0, 0], rot: [Math.PI / 2, 0, 0], noShadow: true }),
        sph(0.045, MAT.green, { pos: [0.11, 0, 0], noShadow: true }, 10),
        sph(0.045, MAT.green, { pos: [-0.055, 0.095, 0], noShadow: true }, 10),
        sph(0.045, MAT.green, { pos: [-0.055, -0.095, 0], noShadow: true }, 10),
    ]);
    rotor.position.set(0, 0.36, 0.20);
    rotor.userData.role = "rotor";
    g.add(rotor);
    g.add(cyl(0.03, 0.03, 0.5, MAT.glass, { pos: [0, 0.62, 0], rot: [0, 0, Math.PI / 2], noShadow: true }));
    g.userData.spinAxis = "z";
    return g;
};
B.pump_submersible = () => {
    const g = group();
    g.add(cyl(0.20, 0.24, 0.62, MAT.steel, { pos: [0, 0.31, 0], tintable: true }));
    g.add(cyl(0.26, 0.26, 0.10, MAT.dark, { pos: [0, 0.05, 0], receive: true }));
    g.add(cyl(0.11, 0.11, 0.16, MAT.metal, { pos: [0.20, 0.50, 0], rot: [0, 0, Math.PI / 2] }));
    const rotor = group([
        cyl(0.05, 0.05, 0.04, MAT.metal, { rot: [Math.PI / 2, 0, 0], noShadow: true }),
        box(0.26, 0.03, 0.02, MAT.green, { noShadow: true }),
        box(0.02, 0.03, 0.26, MAT.green, { noShadow: true }),
    ]);
    rotor.position.set(0, 0.12, 0.14);
    rotor.userData.role = "rotor";
    g.add(rotor);
    g.userData.spinAxis = "z";
    return g;
};
B.dosing_pump = () => {
    const g = group();
    g.add(box(0.42, 0.34, 0.34, MAT.dark, { pos: [0, 0.17, 0], tintable: true, receive: true }));
    g.add(cyl(0.10, 0.10, 0.20, MAT.body, { pos: [0.26, 0.20, 0], rot: [0, 0, Math.PI / 2] }));
    g.add(indicator(0.03, 0.38, MAT.green));
    const rotor = group([
        box(0.16, 0.025, 0.02, MAT.green, { noShadow: true }),
        box(0.02, 0.025, 0.16, MAT.green, { noShadow: true }),
    ]);
    rotor.position.set(0, 0.20, 0.18);
    rotor.userData.role = "rotor";
    g.add(rotor);
    g.userData.spinAxis = "z";
    return g;
};
B.motor = () => {
    const g = group();
    g.add(box(0.66, 0.08, 0.40, MAT.dark, { pos: [0, 0.04, 0], receive: true }));
    g.add(cyl(0.19, 0.19, 0.52, MAT.steel, { pos: [0, 0.30, 0], rot: [0, 0, Math.PI / 2], tintable: true }));
    // sogutma kanatciklari
    for (let i = 0; i < 6; i++) {
        g.add(box(0.44, 0.02, 0.40, MAT.metal, { pos: [0, 0.30 + (i - 2.5) * 0.055, 0], noShadow: true }));
    }
    g.add(box(0.12, 0.18, 0.18, MAT.dark, { pos: [-0.32, 0.30, 0] }));
    g.add(cyl(0.04, 0.04, 0.16, MAT.metal, { pos: [0.33, 0.30, 0], rot: [0, 0, Math.PI / 2] }));
    const rotor = group([
        cyl(0.06, 0.06, 0.03, MAT.metal, { rot: [0, 0, Math.PI / 2], noShadow: true }),
        box(0.03, 0.28, 0.03, MAT.green, { noShadow: true }),
        box(0.03, 0.03, 0.28, MAT.green, { noShadow: true }),
    ]);
    rotor.position.set(0.42, 0.30, 0);
    rotor.userData.role = "rotor";
    g.add(rotor);
    g.userData.spinAxis = "x";
    return g;
};
B.mixer = () => {
    const g = group();
    g.add(box(0.50, 0.08, 0.50, MAT.dark, { pos: [0, 1.30, 0] }));
    g.add(cyl(0.16, 0.16, 0.34, MAT.steel, { pos: [0, 1.51, 0], tintable: true }));
    g.add(cyl(0.04, 0.04, 1.26, MAT.metal, { pos: [0, 0.67, 0] }));
    const rotor = group([
        box(0.44, 0.03, 0.10, MAT.green, { noShadow: true }),
        box(0.10, 0.03, 0.44, MAT.green, { noShadow: true }),
    ]);
    rotor.position.set(0, 0.10, 0);
    rotor.userData.role = "rotor";
    g.add(rotor);
    g.userData.spinAxis = "y";
    return g;
};

/* --- TANKLAR & HAVUZLAR (autoKind: water) --- */
B.tank_vertical = () => {
    const g = group();
    const r = 0.75, h = 2.0;
    g.add(cyl(r, r, h, MAT.steel, { pos: [0, 0.10 + h / 2, 0], tintable: true }));
    g.add(cyl(r * 1.05, r * 1.05, 0.10, MAT.dark, { pos: [0, 0.05, 0], receive: true }));
    g.add(cyl(r * 0.35, r * 0.35, 0.10, MAT.metal, { pos: [0, 0.10 + h + 0.05, 0] }));
    liquidCylinder(g, r * 0.94, h * 0.92, 0.14);
    return g;
};
B.tank_cone = () => {
    const g = group();
    const r = 0.7, h = 1.5;
    g.add(cyl(r, r, h, MAT.steel, { pos: [0, 0.62 + h / 2, 0], tintable: true }));
    g.add(cone(r, 0.56, MAT.steel, { pos: [0, 0.34, 0], rot: [Math.PI, 0, 0] }));
    for (let i = 0; i < 3; i++) {
        const a = (i / 3) * Math.PI * 2;
        g.add(cyl(0.04, 0.04, 0.62, MAT.metal,
                  { pos: [Math.cos(a) * r * 0.8, 0.31, Math.sin(a) * r * 0.8] }));
    }
    liquidCylinder(g, r * 0.93, h * 0.9, 0.68);
    return g;
};
B.basin = () => {
    const g = group();
    const w = 2.6, d = 1.8, h = 0.9;
    // dort duvar + taban (icini gorebilmek icin ust acik)
    g.add(box(w, 0.12, d, MAT.concrete, { pos: [0, 0.06, 0], receive: true, tintable: true }));
    g.add(box(w, h, 0.12, MAT.concrete, { pos: [0, h / 2, -d / 2] , tintable: true }));
    g.add(box(w, h, 0.12, MAT.concrete, { pos: [0, h / 2, d / 2], tintable: true }));
    g.add(box(0.12, h, d, MAT.concrete, { pos: [-w / 2, h / 2, 0], tintable: true }));
    g.add(box(0.12, h, d, MAT.concrete, { pos: [w / 2, h / 2, 0], tintable: true }));
    liquidBox(g, w - 0.26, d - 0.26, h - 0.2, 0.12);
    return g;
};
B.clarifier = () => {
    const g = group();
    const r = 1.6;
    g.add(cyl(r, r, 0.75, MAT.concrete, { pos: [0, 0.375, 0], tintable: true, receive: true }));
    g.add(torus(r, 0.06, MAT.concrete, { pos: [0, 0.75, 0], rot: [Math.PI / 2, 0, 0], noShadow: true }));
    liquidCylinder(g, r * 0.95, 0.6, 0.08);
    // koprulu siyirici -> rotor (yavas doner)
    const rotor = group([
        box(r * 1.9, 0.06, 0.14, MAT.metal, { pos: [0, 0, 0] }),
        cyl(0.10, 0.10, 0.30, MAT.metal, { pos: [0, 0.12, 0] }),
    ]);
    rotor.position.set(0, 0.80, 0);
    rotor.userData.role = "rotor";
    g.add(rotor);
    g.userData.spinAxis = "y";
    return g;
};
B.wet_well = () => {
    const g = group();
    const r = 1.0, h = 1.2;
    g.add(cyl(r, r, h, MAT.concrete, { pos: [0, h / 2, 0], tintable: true, receive: true }));
    g.add(torus(r, 0.07, MAT.dark, { pos: [0, h, 0], rot: [Math.PI / 2, 0, 0], noShadow: true }));
    liquidCylinder(g, r * 0.93, h * 0.9, 0.06);
    return g;
};
B.flow_cell = () => {
    const g = group();
    // Taban + ust bilezik `tintable`: govde CAM oldugundan (boyanmamali, yoksa
    // colorState suyu/cami boyar) durum rengi bu metal parcalara uygulanir.
    g.add(box(0.34, 0.10, 0.26, MAT.dark, { pos: [0, 0.05, 0], receive: true, tintable: true }));
    g.add(cyl(0.13, 0.13, 0.62, MAT.glass, { pos: [0, 0.42, 0] }));
    g.add(cyl(0.15, 0.15, 0.06, MAT.metal, { pos: [0, 0.74, 0], tintable: true }));
    g.add(cyl(0.04, 0.04, 0.16, MAT.metal, { pos: [0.14, 0.20, 0], rot: [0, 0, Math.PI / 2] }));
    g.add(cyl(0.04, 0.04, 0.16, MAT.metal, { pos: [-0.14, 0.66, 0], rot: [0, 0, Math.PI / 2] }));
    liquidCylinder(g, 0.115, 0.56, 0.13);
    return g;
};

/* --- HAVALANDIRMA (autoKind: aeration) --- */
B.aeration = () => {
    const g = group();
    const w = 2.4, d = 1.8, h = 1.0;
    g.add(box(w, 0.12, d, MAT.concrete, { pos: [0, 0.06, 0], receive: true, tintable: true }));
    g.add(box(w, h, 0.12, MAT.concrete, { pos: [0, h / 2, -d / 2], tintable: true }));
    g.add(box(w, h, 0.12, MAT.concrete, { pos: [0, h / 2, d / 2], tintable: true }));
    g.add(box(0.12, h, d, MAT.concrete, { pos: [-w / 2, h / 2, 0], tintable: true }));
    g.add(box(0.12, h, d, MAT.concrete, { pos: [w / 2, h / 2, 0], tintable: true }));
    liquidBox(g, w - 0.26, d - 0.26, h - 0.2, 0.12);
    // difuzor borulari
    for (let i = -1; i <= 1; i++) {
        g.add(cyl(0.05, 0.05, d - 0.4, MAT.metal,
                  { pos: [i * 0.7, 0.20, 0], rot: [Math.PI / 2, 0, 0], noShadow: true }));
    }
    const bub = bubbles(w - 0.4, d - 0.4, h - 0.25, 140);
    bub.position.y = 0.18;
    g.add(bub);
    return g;
};

/* --- BORU & BAGLANTI (autoKind: flow) --- */
B.pipe_h = () => { const g = group([pipeRun(2.0, 0.13, "x")]); g.children[0].position.y = 0.13; return g; };
B.pipe_v = () => {
    const g = group([pipeRun(2.0, 0.13, "y")]);
    g.children[0].position.y = 1.0;
    return g;
};
B.pipe_elbow = () => {
    const g = group();
    const a = pipeRun(1.0, 0.13, "x"); a.position.set(-0.5, 0.13, 0);
    const b = pipeRun(1.0, 0.13, "y"); b.position.set(0, 0.63, 0);
    g.add(a, b);
    g.add(sph(0.15, MAT.metal, { pos: [0, 0.13, 0] }, 14));
    return g;
};
B.pipe_tee = () => {
    const g = group();
    const a = pipeRun(2.0, 0.13, "x"); a.position.y = 0.13;
    const b = pipeRun(0.9, 0.13, "z"); b.position.set(0, 0.13, 0.45);
    g.add(a, b);
    g.add(sph(0.15, MAT.metal, { pos: [0, 0.13, 0] }, 14));
    return g;
};
B.flange = () => {
    const g = group();
    // Govde `pipeRun` ile kurulur: autoKind("flange") = "flow" oldugundan akis
    // kilifi (role="flow") ZORUNLU; elle silindir cizmek bu rolu atlıyordu.
    const run = pipeRun(0.5, 0.13, "x");
    run.position.y = 0.13;
    g.add(run);
    g.add(cyl(0.24, 0.24, 0.05, MAT.steel, { pos: [-0.1, 0.13, 0], rot: [0, 0, Math.PI / 2] }));
    g.add(cyl(0.24, 0.24, 0.05, MAT.steel, { pos: [0.1, 0.13, 0], rot: [0, 0, Math.PI / 2] }));
    for (let i = 0; i < 6; i++) {
        const ang = (i / 6) * Math.PI * 2;
        g.add(cyl(0.022, 0.022, 0.26, MAT.dark,
                  { pos: [0, 0.13 + Math.sin(ang) * 0.18, Math.cos(ang) * 0.18],
                    rot: [0, 0, Math.PI / 2], noShadow: true }, 8));
    }
    return g;
};

/* --- ENSTRUMANLAR (tint / gauge) --- */
B.gauge = () => {
    const g = group();
    g.add(cyl(0.05, 0.05, 0.22, MAT.metal, { pos: [0, 0.11, 0] }));
    g.add(cyl(0.26, 0.26, 0.10, MAT.steel, { pos: [0, 0.44, 0], rot: [Math.PI / 2, 0, 0], tintable: true }));
    g.add(mesh(new THREE.CircleGeometry(0.22, 28), MAT.white,
               { pos: [0, 0.44, 0.055], noShadow: true }));
    // ibre (needle): Z ekseninde doner, 210° -> 330° (2B ile ayni okuma)
    const needle = group([
        box(0.19, 0.018, 0.012, MAT.red, { pos: [0.075, 0, 0], noShadow: true }),
        cyl(0.022, 0.022, 0.02, MAT.dark, { rot: [Math.PI / 2, 0, 0], noShadow: true }),
    ]);
    needle.position.set(0, 0.44, 0.07);
    needle.userData.role = "needle";
    g.add(needle);
    g.userData.needleAxis = "z";
    g.userData.needleMin = THREE.MathUtils.degToRad(210);
    g.userData.needleMax = THREE.MathUtils.degToRad(330);
    return g;
};
B.flowmeter = () => {
    const g = group();
    const p = pipeRun(0.9, 0.13, "x"); p.position.y = 0.13; g.add(p);
    g.add(box(0.30, 0.30, 0.30, MAT.body, { pos: [0, 0.13, 0], tintable: true }));
    g.add(box(0.26, 0.20, 0.06, MAT.dark, { pos: [0, 0.42, 0], tintable: true }));
    g.add(plane(0.20, 0.12, MAT.green, { pos: [0, 0.42, 0.035], noShadow: true }));
    g.add(indicator(0.028, 0.56, MAT.green));
    return g;
};
B.level_sensor = () => {
    const g = group();
    g.add(box(0.24, 0.20, 0.16, MAT.body, { pos: [0, 1.10, 0], tintable: true }));
    g.add(cyl(0.03, 0.03, 1.0, MAT.metal, { pos: [0, 0.50, 0] }));
    g.add(cone(0.07, 0.14, MAT.metal, { pos: [0, 0.07, 0], rot: [Math.PI, 0, 0] }));
    g.add(indicator(0.026, 1.24, MAT.green));
    return g;
};
B.analyzer = () => {
    const g = group();
    g.add(box(0.62, 0.86, 0.34, MAT.body, { pos: [0, 0.43, 0], tintable: true, receive: true }));
    g.add(box(0.50, 0.26, 0.03, MAT.dark, { pos: [0, 0.64, 0.18] }));
    g.add(plane(0.44, 0.20, MAT.green, { pos: [0, 0.64, 0.20], noShadow: true }));
    // 3 durum gostergesi (yalniz bunlar `indicator` -> tint bunlari boyar)
    [MAT.green, MAT.amber, MAT.red].forEach((m, i) => {
        const ind = indicator(0.022, 0.36, m);
        ind.position.set((i - 1) * 0.14, 0.36, 0.18);
        g.add(ind);
    });
    g.add(cyl(0.03, 0.03, 0.24, MAT.metal, { pos: [-0.2, 0.06, 0.18], rot: [Math.PI / 2, 0, 0], noShadow: true }));
    return g;
};
B.ph_sensor = () => {
    const g = group();
    g.add(box(0.16, 0.14, 0.12, MAT.body, { pos: [0, 0.76, 0], tintable: true }));
    g.add(cyl(0.025, 0.025, 0.70, MAT.metal, { pos: [0, 0.35, 0] }));
    g.add(cyl(0.035, 0.035, 0.14, MAT.glass, { pos: [0, 0.07, 0], noShadow: true }));
    g.add(sph(0.035, MAT.glass, { pos: [0, 0.02, 0], noShadow: true }, 12));
    g.add(indicator(0.022, 0.86, MAT.green));
    return g;
};

/* --- ELEKTRIK & PANO (tint) --- */
B.cabinet = () => {
    const g = group();
    g.add(box(1.0, 1.9, 0.44, MAT.body, { pos: [0, 0.95, 0], tintable: true, receive: true }));
    g.add(box(0.94, 1.82, 0.02, MAT.dark, { pos: [0, 0.95, 0.23], noShadow: true }));
    g.add(box(0.04, 0.30, 0.05, MAT.metal, { pos: [0.42, 1.0, 0.25] }));
    const ind = indicator(0.035, 1.72, MAT.green);
    ind.position.set(-0.3, 1.72, 0.24);
    g.add(ind);
    g.add(box(1.06, 0.08, 0.50, MAT.dark, { pos: [0, 0.04, 0], receive: true }));
    return g;
};
B.plc = () => {
    const g = group();
    g.add(box(0.70, 0.34, 0.18, MAT.dark, { pos: [0, 0.17, 0], tintable: true, receive: true }));
    for (let i = 0; i < 5; i++) {
        g.add(box(0.11, 0.30, 0.14, MAT.body, { pos: [-0.26 + i * 0.13, 0.17, 0.03] }));
    }
    for (let i = 0; i < 5; i++) {
        const ind = indicator(0.016, 0.28, MAT.green);
        ind.position.set(-0.26 + i * 0.13, 0.28, 0.11);
        g.add(ind);
    }
    return g;
};
B.lamp = () => {
    const g = group();
    g.add(cyl(0.09, 0.11, 0.14, MAT.dark, { pos: [0, 0.07, 0], receive: true }));
    const dome = sph(0.10, MAT.green, { pos: [0, 0.20, 0] }, 16);
    dome.userData.role = "indicator";
    dome.userData.tintable = true;
    g.add(dome);
    return g;
};

/* --- GUVENLIK & ALARM (blink) --- */
B.beacon = () => {
    const g = group();
    g.add(cyl(0.11, 0.13, 0.12, MAT.dark, { pos: [0, 0.06, 0], receive: true }));
    const dome = cyl(0.10, 0.12, 0.22, MAT.amber, { pos: [0, 0.23, 0] });
    dome.userData.role = "indicator";
    dome.userData.tintable = true;
    g.add(dome);
    g.add(sph(0.10, MAT.amber, { pos: [0, 0.34, 0], noShadow: true }, 14));
    return g;
};
B.emergency_stop = () => {
    const g = group();
    g.add(box(0.34, 0.34, 0.14, MAT.amber, { pos: [0, 0.17, 0], tintable: true, receive: true }));
    const btn = cyl(0.11, 0.11, 0.06, MAT.red, { pos: [0, 0.17, 0.10], rot: [Math.PI / 2, 0, 0] });
    btn.userData.role = "indicator";
    btn.userData.tintable = true;
    g.add(btn);
    return g;
};

/* --- NUMUNE BUZDOLABI (carousel) --- */
B.sample_fridge = () => {
    const g = group();
    g.add(box(0.90, 1.10, 0.80, MAT.body, { pos: [0, 0.55, 0], tintable: true, receive: true }));
    g.add(box(0.84, 0.60, 0.03, MAT.glass, { pos: [0, 0.72, 0.41], noShadow: true }));
    g.add(box(0.94, 0.08, 0.86, MAT.dark, { pos: [0, 0.04, 0], receive: true }));
    // carousel: donen numune tepsisi
    const car = group();
    car.add(cyl(0.34, 0.34, 0.03, MAT.metal, { noShadow: true }));
    for (let i = 0; i < 8; i++) {
        const a = (i / 8) * Math.PI * 2;
        car.add(cyl(0.05, 0.05, 0.14, MAT.glass,
                    { pos: [Math.cos(a) * 0.24, 0.08, Math.sin(a) * 0.24], noShadow: true }, 10));
    }
    car.position.set(0, 0.42, 0);
    car.userData.role = "carousel";
    g.add(car);
    g.add(indicator(0.025, 1.14, MAT.green));
    return g;
};

/* --- YIKAMA BARI (washbar) --- */
B.wash_bar = () => {
    const g = group();
    const w = 1.1;
    g.add(cyl(0.05, 0.05, w, MAT.metal, { pos: [0, 0.60, 0], rot: [0, 0, Math.PI / 2], tintable: true }));
    g.add(cyl(0.04, 0.04, 0.6, MAT.metal, { pos: [-w / 2, 0.30, 0] }));
    for (let i = 0; i < 6; i++) {
        g.add(cyl(0.018, 0.026, 0.05, MAT.dark,
                  { pos: [-w / 2 + 0.1 + i * 0.18, 0.56, 0], noShadow: true }, 8));
    }
    const sp = spray(w, 90);
    sp.position.set(0, 0.54, 0);
    g.add(sp);
    return g;
};

/* --- KAPI (door) --- */
B.door = () => {
    const g = group();
    const w = 1.3, h = 2.1;
    g.add(box(w + 0.16, 0.08, 0.22, MAT.dark, { pos: [0, h + 0.04, 0] }));
    g.add(box(0.08, h, 0.22, MAT.dark, { pos: [-w / 2 - 0.04, h / 2, 0] }));
    g.add(box(0.08, h, 0.22, MAT.dark, { pos: [w / 2 + 0.04, h / 2, 0] }));
    g.add(box(w, h, 0.02, MAT.dark, { pos: [0, h / 2, -0.06], noShadow: true }));
    const leafL = box(w / 2, h - 0.04, 0.06, MAT.body, { pos: [-w / 4, h / 2, 0], tintable: true });
    leafL.userData.role = "doorLeafL";
    const leafR = box(w / 2, h - 0.04, 0.06, MAT.body, { pos: [w / 4, h / 2, 0], tintable: true });
    leafR.userData.role = "doorLeafR";
    g.add(leafL, leafR);
    g.userData.doorTravel = w / 2;
    return g;
};

/* ------------------------------------------------------------------ */
/* Katalog (palet paneli bunu okur)                                    */
/* ------------------------------------------------------------------ */
export const M3D_SYMBOLS = [
    { group: "valves", label: "Vanalar", items: [
        { key: "valve_gate", name: "Sürgülü Vana", size: [0.9, 0.6, 0.35] },
        { key: "valve_ball", name: "Küresel Vana", size: [0.9, 0.6, 0.35] },
        { key: "valve_butterfly", name: "Kelebek Vana", size: [0.9, 0.6, 0.35] },
        { key: "valve_solenoid", name: "Solenoid Vana", size: [0.9, 0.75, 0.35] },
    ] },
    { group: "pumps", label: "Pompalar & Motorlar", items: [
        { key: "pump_centrifugal", name: "Santrifüj Pompa", size: [0.9, 0.6, 0.55] },
        { key: "pump_peristaltic", name: "Peristaltik Pompa", size: [0.6, 0.9, 0.45] },
        { key: "pump_submersible", name: "Dalgıç Pompa", size: [0.5, 0.65, 0.5] },
        { key: "dosing_pump", name: "Dozaj Pompası", size: [0.5, 0.45, 0.35] },
        { key: "motor", name: "Motor", size: [0.7, 0.6, 0.4] },
        { key: "mixer", name: "Karıştırıcı", size: [0.5, 1.7, 0.5] },
    ] },
    { group: "tanks", label: "Tanklar & Havuzlar", items: [
        { key: "tank_vertical", name: "Dikey Tank", size: [1.6, 2.2, 1.6] },
        { key: "tank_cone", name: "Konik Tank", size: [1.5, 2.2, 1.5] },
        { key: "basin", name: "Havuz", size: [2.6, 0.9, 1.8] },
        { key: "clarifier", name: "Çöktürme Havuzu", size: [3.2, 1.1, 3.2] },
        { key: "wet_well", name: "Terfi Kuyusu", size: [2.0, 1.2, 2.0] },
        { key: "flow_cell", name: "Akış Hücresi", size: [0.4, 0.8, 0.3] },
        { key: "aeration", name: "Havalandırma Havuzu", size: [2.4, 1.0, 1.8] },
    ] },
    { group: "pipes", label: "Boru & Bağlantı", items: [
        { key: "pipe_h", name: "Boru (Yatay)", size: [2.0, 0.26, 0.26] },
        { key: "pipe_v", name: "Boru (Düşey)", size: [0.26, 2.0, 0.26] },
        { key: "pipe_elbow", name: "Dirsek", size: [1.0, 1.1, 0.26] },
        { key: "pipe_tee", name: "Te Bağlantı", size: [2.0, 0.26, 0.9] },
        { key: "flange", name: "Flanş", size: [0.5, 0.5, 0.5] },
    ] },
    { group: "instruments", label: "Enstrümanlar", items: [
        { key: "gauge", name: "Manometre", size: [0.5, 0.7, 0.15] },
        { key: "flowmeter", name: "Debimetre", size: [0.9, 0.6, 0.35] },
        { key: "level_sensor", name: "Seviye Sensörü", size: [0.25, 1.3, 0.2] },
        { key: "analyzer", name: "Analizör", size: [0.65, 0.9, 0.35] },
        { key: "ph_sensor", name: "pH Sensörü", size: [0.2, 0.9, 0.15] },
    ] },
    { group: "electrical", label: "Elektrik & Pano", items: [
        { key: "cabinet", name: "Pano", size: [1.05, 1.9, 0.5] },
        { key: "plc", name: "PLC", size: [0.7, 0.35, 0.2] },
        { key: "lamp", name: "Sinyal Lambası", size: [0.22, 0.3, 0.22] },
    ] },
    { group: "safety", label: "Güvenlik & Saha", items: [
        { key: "beacon", name: "Çakar Lamba", size: [0.26, 0.45, 0.26] },
        { key: "emergency_stop", name: "Acil Stop", size: [0.35, 0.35, 0.15] },
        { key: "sample_fridge", name: "Numune Buzdolabı", size: [0.95, 1.15, 0.85] },
        { key: "wash_bar", name: "Yıkama Barı", size: [1.1, 0.65, 0.15] },
        { key: "door", name: "Kapı", size: [1.5, 2.2, 0.25] },
    ] },
];

const META = {};
M3D_SYMBOLS.forEach((grp) => {
    grp.items.forEach((it) => {
        META[it.key] = Object.assign({ group: grp.group, groupLabel: grp.label }, it);
    });
});

/**
 * Sozlesme normalizasyonu: cocuklari, sinir kutusunun **tabani y=0** ve
 * **X/Z merkezi orijinde** olacak sekilde kaydirir.
 *
 * Her builder'in bunu elle tutturmasini beklemek kirilgan (mixer'in mili
 * tabandan 4 cm yukarida basliyordu, flans civatalari y=0'in ALTINA tasiyordu,
 * dirsek/te X-Z'de merkezli degildi). Merkezi olarak yapmak bugunku 35 sembol
 * icin oldugu kadar SONRADAN EKLENECEK semboller icin de garanti verir; boylece
 * izgaraya birakma her sembolde ayni davranir ve sembol basina ofset tablosu
 * gerekmez.
 */
function normalizeSymbol(g) {
    g.updateMatrixWorld(true);
    const b = new THREE.Box3().setFromObject(g);
    if (b.isEmpty()) return g;
    const c = b.getCenter(new THREE.Vector3());
    const dx = -c.x, dy = -b.min.y, dz = -c.z;
    if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6 && Math.abs(dz) < 1e-6) return g;
    g.children.forEach((ch) => {
        ch.position.x += dx;
        ch.position.y += dy;
        ch.position.z += dz;
    });
    // Sivi parametreleri de kaydirilmis olmali (runtime bunlari kullanir).
    if (typeof g.userData.liqBottom === "number") g.userData.liqBottom += dy;
    if (typeof g.userData.liqTop === "number") g.userData.liqTop += dy;
    g.updateMatrixWorld(true);
    return g;
}

/** Sembolu uret. Bilinmeyen anahtarda null (cagiran yer tutucu koyar). */
export function buildSymbol(key) {
    const fn = B[key];
    if (!fn) return null;
    const g = normalizeSymbol(fn());
    g.userData.symbolKey = key;
    g.name = (META[key] && META[key].name) || key;
    return g;
}

export function symbolMeta(key) { return META[key] || null; }
export function symbolKeys() { return Object.keys(B); }

/**
 * Palet ikonu: sembolu kucuk bir offscreen hedefe render edip dataURL dondurur.
 * Sonuclar `sessionStorage`'da onbelleklenir -> ikinci sayfa yuklemesi anlik.
 */
export function symbolThumbURL(key, renderer, size) {
    const px = size || 96;
    const ck = "mimic3d.thumbs.v1." + key + "." + px;
    try {
        const hit = sessionStorage.getItem(ck);
        if (hit) return hit;
    } catch (e) { /* private mode */ }

    const obj = buildSymbol(key);
    if (!obj) return "";
    const scene = new THREE.Scene();
    scene.add(new THREE.HemisphereLight(0xdfe8f5, 0x3a3f48, 1.1));
    const dl = new THREE.DirectionalLight(0xffffff, 1.4);
    dl.position.set(3, 5, 4);
    scene.add(dl);
    scene.add(obj);

    const bbox = new THREE.Box3().setFromObject(obj);
    const c = bbox.getCenter(new THREE.Vector3());
    const r = Math.max(0.3, bbox.getSize(new THREE.Vector3()).length() * 0.5);
    const cam = new THREE.PerspectiveCamera(38, 1, 0.05, 100);
    const dist = (r / Math.sin((38 * Math.PI) / 180 / 2)) * 1.12;
    cam.position.set(c.x + dist * 0.62, c.y + dist * 0.52, c.z + dist * 0.66);
    cam.lookAt(c);

    const rt = new THREE.WebGLRenderTarget(px, px, { depthBuffer: true });
    const prevTarget = renderer.getRenderTarget();
    renderer.setRenderTarget(rt);
    renderer.setClearColor(0x1b1f27, 0);
    renderer.clear();
    renderer.render(scene, cam);
    renderer.setRenderTarget(prevTarget);

    const buf = new Uint8Array(px * px * 4);
    renderer.readRenderTargetPixels(rt, 0, 0, px, px, buf);
    // WebGL alttan ust okur -> dikey cevir
    const cv = document.createElement("canvas");
    cv.width = px; cv.height = px;
    const ctx = cv.getContext("2d");
    const img = ctx.createImageData(px, px);
    for (let y = 0; y < px; y++) {
        const src = (px - 1 - y) * px * 4;
        const dst = y * px * 4;
        for (let x = 0; x < px * 4; x++) img.data[dst + x] = buf[src + x];
    }
    ctx.putImageData(img, 0, 0);
    const url = cv.toDataURL("image/png");

    rt.dispose();
    // Sembolun KENDI (paylasilmayan) kaynaklarini serbest birak; palet
    // material'lari userData.shared oldugundan korunur.
    obj.traverse((o) => {
        if (o.geometry && !(o.geometry.userData && o.geometry.userData.shared)) o.geometry.dispose();
        const ms = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
        ms.forEach((m) => { if (m && !(m.userData && m.userData.shared)) m.dispose(); });
    });

    try { sessionStorage.setItem(ck, url); } catch (e) { /* kota */ }
    return url;
}

export default { M3D_SYMBOLS, buildSymbol, symbolMeta, symbolKeys, symbolThumbURL, MAT, M3D_TEX, M3D_PALETTE };
