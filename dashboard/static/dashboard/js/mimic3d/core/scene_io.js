/**
 * mimic3d/core/scene_io.js — sahne <-> belge (`mimic3d/1`) donusumu.
 *
 * `fromDoc` iki gecisli: (1) tum nesneleri uret, (2) `parent` referanslariyla
 * hiyerarsiyi kur. Bu, belgede ebeveynin cocuktan SONRA gelmesine izin verir ve
 * dongusel/kirik referanslarda coküp kullanicinin tasarimini kaybetmez.
 *
 * `toDoc` duz liste uretir; transform'lar 4 ondaliga yuvarlanir (~%30 bayt
 * kazanci, gorsel etki yok) ve varsayilanina esit `scada` anahtarlari atlanir.
 */

import { EDITOR_VERSION, SCHEMA, defaultScada, newId, r4 } from "./doc.js";
import { buildFromEntry } from "./factory.js";
import { clearChildren } from "./dispose.js";

/** Sahnedeki tum belge nesneleri (ic mesh'ler DEGIL, yalniz docId tasiyanlar). */
export function docObjects(contentRoot) {
    const out = [];
    contentRoot.traverse((o) => {
        if (o.userData && o.userData.docId) out.push(o);
    });
    return out;
}

/** docId -> Object3D haritasi. */
export function docMap(contentRoot) {
    const m = new Map();
    docObjects(contentRoot).forEach((o) => m.set(o.userData.docId, o));
    return m;
}

/**
 * Belgeyi sahneye yukle. Mevcut icerik dispose edilir.
 * @returns {object} {count, warnings}
 */
export function fromDoc(stage, doc, ctx) {
    const warnings = [];
    clearChildren(stage.contentRoot);

    const entries = (doc && doc.objects) || [];
    const built = new Map();

    // 1. gecis: uret
    entries.forEach((e) => {
        if (!e || !e.type) return;
        if (!e.id) e.id = newId(e.type ? e.type[0] : "o");
        const obj = buildFromEntry(e, ctx);
        if (!obj) {
            // Ileri uyumluluk: bilinmeyen tip SESSIZCE atlanmaz ama HATA da atmaz.
            warnings.push('Bilinmeyen nesne tipi atlandı: "' + e.type + '"');
            return;
        }
        built.set(e.id, obj);
    });

    // 2. gecis: hiyerarsi
    entries.forEach((e) => {
        const obj = built.get(e && e.id);
        if (!obj) return;
        const parent = e.parent ? built.get(e.parent) : null;
        if (e.parent && !parent) {
            warnings.push('Nesnenin ebeveyni bulunamadı, köke alındı: "' + (e.name || e.id) + '"');
        }
        // add (attach DEGIL): belgedeki transform'lar ZATEN yereldir.
        (parent || stage.contentRoot).add(obj);
    });

    return { count: built.size, warnings: warnings };
}

function scadaDiff(sc) {
    // Varsayilanindan farkli anahtarlari yaz (belge kucuk kalsin).
    const def = defaultScada();
    const out = {};
    Object.keys(sc || {}).forEach((k) => {
        if (k === "menu") return;
        if (sc[k] !== undefined && sc[k] !== def[k]) out[k] = sc[k];
    });
    const mn = sc && sc.menu;
    if (mn && (mn.enabled || mn.historic || mn.report || mn.daily || mn.control)) {
        out.menu = {
            enabled: !!mn.enabled, historic: !!mn.historic, report: !!mn.report,
            daily: !!mn.daily, control: !!mn.control,
        };
    }
    return out;
}

function materialOf(obj) {
    // Nesnenin "birincil" material'i: ilk tintable mesh, yoksa ilk mesh.
    let first = null, tint = null;
    obj.traverse((o) => {
        if (!o.isMesh || Array.isArray(o.material) || !o.material) return;
        if (!first) first = o;
        if (!tint && o.userData && o.userData.tintable) tint = o;
    });
    const m = (tint || first);
    return m ? m.material : null;
}

/** Tek nesneyi belge girdisine cevir. */
export function entryOf(obj, parentId) {
    const t = obj.userData.docType;
    const e = {
        id: obj.userData.docId,
        type: t,
        name: obj.name || t,
        parent: parentId || null,
        position: obj.position.toArray().map(r4),
        rotation: [r4(obj.rotation.x), r4(obj.rotation.y), r4(obj.rotation.z)],
        scale: obj.scale.toArray().map(r4),
    };
    if (obj.visible === false) e.visible = false;
    if (obj.userData.locked) e.locked = true;

    if (t === "primitive") {
        e.prim = obj.userData.prim || "box";
        e.geo = obj.userData.geo || undefined;
        const mat = obj.material;
        if (mat) {
            e.material = {
                color: "#" + mat.color.getHexString(),
                metalness: r4(mat.metalness),
                roughness: r4(mat.roughness),
                opacity: r4(mat.opacity),
                transparent: !!mat.transparent,
                wireframe: !!mat.wireframe,
            };
        }
    } else if (t === "label") {
        e.text = Object.assign({}, obj.userData.labelSpec);
    } else if (t === "button") {
        e.button = Object.assign({}, obj.userData.buttonSpec);
    } else if (t === "symbol") {
        e.symbolKey = obj.userData.symbolKey || "";
        // material override: yalniz kullanici degistirdiyse (own material) yaz.
        const mat = materialOf(obj);
        if (mat && mat.userData && mat.userData.shared === false) {
            e.material = { color: "#" + mat.color.getHexString(), opacity: r4(mat.opacity) };
        }
    }

    const sc = scadaDiff(obj.scada);
    if (Object.keys(sc).length) e.scada = sc;
    return e;
}

/** Sahneyi tam belgeye cevir (kaydetme + undo snapshot'i). */
export function toDoc(stage, base) {
    const doc = Object.assign({}, base || {});
    doc.schema = SCHEMA;
    doc.kind = "3d";
    doc.renderer = "three";
    doc.units = "m";
    doc.scene = stage.readSceneSettings();
    doc.camera = stage.readCameraSettings();
    doc.viewpoints = (base && base.viewpoints) || [];
    doc.paths = (base && base.paths) || [];

    const objects = [];
    function walk(node, parentId) {
        node.children.forEach((c) => {
            if (!c.userData || !c.userData.docId) return;
            objects.push(entryOf(c, parentId));
            walk(c, c.userData.docId);
        });
    }
    walk(stage.contentRoot, null);
    doc.objects = objects;
    doc.meta = {
        objectCount: objects.length,
        editorVersion: EDITOR_VERSION,
        bytes: 0,
    };
    // bytes: kendini iceren alan oldugundan iki adimda hesaplanir.
    doc.meta.bytes = JSON.stringify(doc).length;
    return doc;
}

/**
 * Bir alt agaci kopyalanabilir girdi listesine cevirir (clipboard / duplicate).
 * Id'ler YENIDEN uretilir, `parent` referanslari yeniden eslenir.
 */
export function subtreeEntries(objs) {
    const out = [];
    const idMap = new Map();

    function emit(obj, parentId) {
        const oldId = obj.userData.docId;
        const e = entryOf(obj, parentId);
        const nid = newId(e.type ? e.type[0] : "o");
        idMap.set(oldId, nid);
        e.id = nid;
        out.push(e);
        obj.children.forEach((c) => {
            if (c.userData && c.userData.docId) emit(c, nid);
        });
    }
    (objs || []).forEach((o) => emit(o, null));
    return out;
}
