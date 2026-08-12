/**
 * mimic3d/core/doc.js — 3B sahne belgesi (schema "mimic3d/1") sabitleri.
 *
 * Belge `MimicScreen.data` alaninda saklanir (kind="3d"). Sozlesme:
 *
 *  - **Duz nesne listesi + `parent` id referansi** (ic ice DEGIL): undo
 *    snapshot'lari ucuz serialize edilir, outliner tek gecisle kurulur, secim
 *    ve animasyon tek `Map<id, Object3D>` uzerinden calisir, JSON derinligi
 *    gruplama derinliginden bagimsiz sabit kalir.
 *  - **Birim metre, Y yukari, taban y=0.** Her sembol builder'i footprint'i
 *    X/Z merkezinde ve tabani y=0'da uretir -> izgaraya birakma (drop) icin
 *    sembol basina ofset tablosu gerekmez.
 *  - **`scada` blogu 2B ile BIREBIR AYNI** (+ 4 eklemeli anahtar: axis, pathId,
 *    viewpointId, orient) -> `MimicCore.autoKind` ve animasyon semantigi
 *    degismeden tasinir.
 *  - `material` alanindaki `null` = "sembolun kendi paletini miras al" (sembol
 *    builder'i iyilestirilince kayitli ekranlar da iyilesir).
 *  - `roles` belgede TUTULMAZ; rol meta verisi builder'da yasar.
 *
 * Yonlendirmenin tek dogruluk kaynagi DB'deki `MimicScreen.kind` kolonudur;
 * buradaki `schema`/`kind` alanlari yalniz disa aktarilan .json dosyasinin
 * dogru editore yonlendirilmesi icindir.
 */

export const SCHEMA = "mimic3d/1";
export const EDITOR_VERSION = "1.0.0";

/** Nesne tipleri. Bilinmeyen tip yuklemede UYARIYLA ATLANIR (ileri uyumluluk). */
export const TYPES = ["symbol", "primitive", "group", "label", "button", "import", "sceneBinding"];

/** `type:"primitive"` alt turleri — 2B'deki "Ekle" grubunun karsiligi. */
export const PRIMS = ["box", "sphere", "cylinder", "cone", "torus", "plane", "pipe", "arrow"];

/** Golge kalitesi -> shadow map boyutu (0 = golge kapali). */
export const SHADOW_QUALITY = { off: 0, low: 0, medium: 1024, high: 2048 };

/** Performans tavanlari (bkz. CLAUDE.md — mutevazi saha PC'si hedefi). */
export const LIMITS = {
    warnObjects: 200,     // durum cubugunda uyari
    maxObjects: 500,      // kayit bloke
    maxPixelRatio: 1.5,
    maxCaptureMegapixels: 33,
};

/** Yeni/bos sahnenin varsayilan ayarlari. */
export function defaultScene() {
    return {
        // Not: sahne ACES tone mapping ile render edilir; ham hex'ler ekranda
        // bir miktar KOYULASIR. Varsayilanlar bunu telafi edecek sekilde secildi
        // (aksi halde gokyuzu duz siyah gibi okunuyordu).
        background: { mode: "gradient", color: "#141b26", top: "#3d5f80", bottom: "#0e141c" },
        environment: { preset: "room", intensity: 0.55 },
        fog: { enabled: false, color: "#0f1420", near: 20, far: 160 },
        grid: { visible: true, size: 40, divisions: 40, color1: "#3a4250", color2: "#252b36" },
        ground: {
            visible: true, size: 40, color: "#20242d",
            roughness: 0.92, metalness: 0.0, receiveShadow: true,
        },
        lights: {
            ambient: { color: "#ffffff", intensity: 0.40 },
            hemi: { skyColor: "#dfe8f5", groundColor: "#3a3f48", intensity: 0.35 },
            dir: {
                color: "#ffffff", intensity: 1.15,
                position: [12, 18, 9], target: [0, 0, 0], castShadow: true,
            },
        },
        shadows: { enabled: true, quality: "medium", bias: -0.0005 },
        toneMapping: "aces",
        exposure: 1.0,
    };
}

export function defaultCamera() {
    return {
        type: "perspective", fov: 50, near: 0.1, far: 400,
        position: [13.5, 9.0, 16.0], target: [0, 1.2, 0],
        orthoZoom: 40,
        limits: { minDistance: 1.5, maxDistance: 180, maxPolarAngle: 1.5533 },
    };
}

export function defaultDoc() {
    return {
        schema: SCHEMA,
        kind: "3d",
        renderer: "three",
        units: "m",
        scene: defaultScene(),
        camera: defaultCamera(),
        viewpoints: [],
        paths: [],
        objects: [],
        meta: { objectCount: 0, editorVersion: EDITOR_VERSION },
    };
}

/** `scada` blogu varsayilanlari — 2B `SCADA_DEFAULTS` ile ayni, 3B farklari not'lu. */
export function defaultScada() {
    return {
        tag: "", expr: "", anim: "none",
        min: 0, max: 100, threshold: 1,
        onColor: "#3fbf6f", offColor: "#e4544c",
        speed: 1, unit: "", decimals: 1, showUnit: true,
        // DIKKAT: 2B'de moveRange piksel (60); 3B'de SAHNE BIRIMI (metre) -> 1.0.
        moveRange: 1.0,
        axis: "y",
        menu: { enabled: false, historic: false, report: false, daily: false, control: false },
    };
}

let _idSeq = 0;

/** Belge-ici benzersiz id. Yapistir/undo id'leri yeniden uretir. */
export function newId(prefix) {
    _idSeq += 1;
    return (prefix || "o") + "_" + _idSeq.toString(36) + Math.floor(performance.now() % 1e6).toString(36);
}

/**
 * Belgeyi guncel semaya tasi / dogrula.
 *
 * Tolerans kasitli: kayitli bir sahnenin acilmamasi, kucuk bir sema
 * uyusmazligindan cok daha kotudur.
 *  - bos/null                        -> varsayilan belge
 *  - `objects` var ama `schema` yok   -> mimic3d/1 kabul et
 *  - daha YENI minor sema            -> yukle + uyari dondur
 *  - 2B (Fabric) belgesi             -> hata dondur (cagiran net mesaj basar)
 */
export function migrate(raw) {
    const warnings = [];
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
        return { doc: defaultDoc(), warnings, error: null };
    }
    // Fabric belgesi mi? (2B: {version, objects:[{type:"rect",...}], background})
    const looksFabric = !raw.schema && Array.isArray(raw.objects)
        && raw.objects.some((o) => o && typeof o.type === "string" && o.left !== undefined);
    if (looksFabric) {
        return { doc: defaultDoc(), warnings, error: "2d-document" };
    }

    const doc = defaultDoc();
    if (raw.schema && raw.schema !== SCHEMA) {
        warnings.push("Sahne şeması farklı (" + raw.schema + "); uyumlu okumaya çalışıldı.");
    }
    doc.scene = deepMerge(doc.scene, raw.scene);
    doc.camera = deepMerge(doc.camera, raw.camera);
    doc.viewpoints = Array.isArray(raw.viewpoints) ? raw.viewpoints.slice() : [];
    doc.paths = Array.isArray(raw.paths) ? raw.paths.slice() : [];
    doc.objects = Array.isArray(raw.objects) ? raw.objects.slice() : [];
    doc.meta = Object.assign({}, doc.meta, raw.meta || {});
    return { doc, warnings, error: null };
}

/** Duz-obje birlestirme (dizi = deger, obje = ic ice birlestir). */
export function deepMerge(base, over) {
    if (!over || typeof over !== "object") return base;
    const out = Array.isArray(base) ? base.slice() : Object.assign({}, base);
    Object.keys(over).forEach((k) => {
        const bv = base ? base[k] : undefined;
        const ov = over[k];
        if (ov && typeof ov === "object" && !Array.isArray(ov)
            && bv && typeof bv === "object" && !Array.isArray(bv)) {
            out[k] = deepMerge(bv, ov);
        } else if (ov !== undefined) {
            out[k] = ov;
        }
    });
    return out;
}

/** Serialize'da kullanilan yuvarlama — 4 ondalik ~%30 bayt kazandirir, gorsel etkisi yok. */
export function r4(n) {
    return Math.round((Number(n) || 0) * 1e4) / 1e4;
}

export function vec3(v, fallback) {
    if (Array.isArray(v) && v.length >= 3) return [r4(v[0]), r4(v[1]), r4(v[2])];
    return (fallback || [0, 0, 0]).slice();
}
